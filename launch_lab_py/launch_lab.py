import base64
import os
import struct
from typing import Optional

from solana.rpc.commitment import Processed
from solana.rpc.types import TokenAccountOpts, TxOpts

from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price  # type: ignore
from solders.instruction import AccountMeta, Instruction  # type: ignore
from solders.message import MessageV0  # type: ignore
from solders.pubkey import Pubkey  # type: ignore
from solders.system_program import CreateAccountWithSeedParams, create_account_with_seed  # type: ignore
from solders.transaction import VersionedTransaction  # type: ignore

from spl.token.client import Token
from spl.token.instructions import (
    CloseAccountParams,
    InitializeAccountParams,
    close_account,
    create_associated_token_account,
    get_associated_token_address,
    initialize_account,
)

from config import client, payer_keypair, UNIT_BUDGET, UNIT_PRICE
from constants import *
from common_utils import confirm_txn, get_token_balance
from pool_utils import *


def buy(pool_str: str, sol_in: float = 0.1, slippage: int = 5) -> bool:
    try:
        print(f"Starting buy transaction for pool: {pool_str}")

        print("Fetching pool state...")
        pool_state: Optional[PoolState] = fetch_pool_state(pool_str)
        
        if pool_state is None:
            print("No pool state found, aborting transaction.")
            return False
        print("Pool state fetched successfully.")

        if pool_state.status != 0:
            print("This pool is no longer tradable on Launch Lab - it has migrated to Raydium CPMM...")
            return
        
        if pool_state.global_config != GLOBAL_CONFIG:
            print("Only Constant Product Curve is supported at this time...")
            return

        print("Calculating transaction amounts...")
        sol_decimal = 1e9
        token_decimal = 10 ** pool_state.base_decimals
        slippage_adjustment = 1 - (slippage / 100)
        
        amount_in = int(sol_in * sol_decimal)
        print(f"Amount in (SOL): {sol_in} | Lamports: {amount_in}")

        # Fee setup
        if pool_state.platform_config == RAYDIUM_PLATFORM:
            protocol_fee_pct = 0.25
            platform_fee_pct = 0.75
        else:
            protocol_fee_pct = 0.25
            platform_fee_pct = 1  

        raw_amount_out = constant_product_buy_exact_in(
            pool_state.virtual_base, 
            pool_state.virtual_quote, 
            pool_state.real_base, 
            pool_state.real_quote, 
            amount_in,
            protocol_fee_pct, 
            platform_fee_pct,
            0
        )

        minimum_amount_out = int(raw_amount_out * slippage_adjustment)

        print(f"Expected amount out (before slippage): {raw_amount_out / token_decimal}")
        print(f"Minimum amount out (after {slippage}% slippage): {minimum_amount_out / token_decimal}")

        print("Checking for existing token account...")
        token_account_check = client.get_token_accounts_by_owner(
            payer_keypair.pubkey(), 
            TokenAccountOpts(pool_state.base_mint), 
            Processed
            )
        
        if token_account_check.value:
            token_account = token_account_check.value[0].pubkey
            token_account_instruction = None
            print("Existing token account found.")
        else:
            token_account = get_associated_token_address(payer_keypair.pubkey(), pool_state.base_mint)
            token_account_instruction = create_associated_token_account(
                payer_keypair.pubkey(), 
                payer_keypair.pubkey(), 
                pool_state.base_mint
                )
            print("No existing token account found; creating associated token account.")

        print("Generating seed for WSOL account...")
        seed = base64.urlsafe_b64encode(os.urandom(24)).decode("utf-8")
        wsol_token_account = Pubkey.create_with_seed(payer_keypair.pubkey(), seed, TOKEN_PROGRAM_ID)
        balance_needed = Token.get_min_balance_rent_for_exempt_for_account(client)

        print("Creating and initializing WSOL account...")
        create_wsol_account_instruction = create_account_with_seed(
            CreateAccountWithSeedParams(
                from_pubkey=payer_keypair.pubkey(),
                to_pubkey=wsol_token_account,
                base=payer_keypair.pubkey(),
                seed=seed,
                lamports=int(balance_needed + amount_in),
                space=ACCOUNT_SPACE,
                owner=TOKEN_PROGRAM_ID,
            )
        )

        init_wsol_account_instruction = initialize_account(
            InitializeAccountParams(
                program_id=TOKEN_PROGRAM_ID,
                account=wsol_token_account,
                mint=WSOL,
                owner=payer_keypair.pubkey(),
            )
        )

        print("Creating swap instructions...")
        accounts = [
            AccountMeta(payer_keypair.pubkey(), True, True),
            AccountMeta(AUTHORITY, False, False),
            AccountMeta(pool_state.global_config, False, False),
            AccountMeta(pool_state.platform_config, False, False),
            AccountMeta(pool_state.pool, False, True),
            AccountMeta(token_account, False, True),
            AccountMeta(wsol_token_account, False, True),
            AccountMeta(pool_state.base_vault, False, True),
            AccountMeta(pool_state.quote_vault, False, True),
            AccountMeta(pool_state.base_mint, False, False),
            AccountMeta(pool_state.quote_mint, False, False),
            AccountMeta(TOKEN_PROGRAM_ID, False, False),  
            AccountMeta(TOKEN_PROGRAM_ID, False, False),
            AccountMeta(EVENT_AUTH, False, False),
            AccountMeta(PROGRAM_ID, False, False),
        ]

        data = bytearray()
        data.extend(bytes.fromhex("faea0d7bd59c13ec"))
        data.extend(struct.pack('<Q', amount_in))
        data.extend(struct.pack('<Q', minimum_amount_out))
        data.extend(struct.pack('<Q', 0))
        swap_instruction = Instruction(PROGRAM_ID, bytes(data), accounts)

        print("Preparing to close WSOL account after swap...")
        close_wsol_account_instruction = close_account(
            CloseAccountParams(
                program_id=TOKEN_PROGRAM_ID,
                account=wsol_token_account,
                dest=payer_keypair.pubkey(),
                owner=payer_keypair.pubkey(),
            )
        )

        instructions = [
            set_compute_unit_limit(UNIT_BUDGET),
            set_compute_unit_price(UNIT_PRICE),
            create_wsol_account_instruction,
            init_wsol_account_instruction,
        ]

        if token_account_instruction:
            instructions.append(token_account_instruction)

        instructions.append(swap_instruction)
        instructions.append(close_wsol_account_instruction)
        
        print("Compiling transaction message...")
        compiled_message = MessageV0.try_compile(
            payer_keypair.pubkey(),
            instructions,
            [],
            client.get_latest_blockhash().value.blockhash,
        )

        print("Sending transaction...")
        txn_sig = client.send_transaction(txn=VersionedTransaction(compiled_message, [payer_keypair]), opts=TxOpts(skip_preflight=False)).value
        print(f"Transaction Signature: {txn_sig}")
        
        print("Confirming transaction...")
        confirmed = confirm_txn(txn_sig)
        
        print(f"Transaction confirmed: {confirmed}")
        return confirmed
    except Exception as e:
        print("Error occurred during transaction:", e)
        return False

def sell(pool_str: str, percentage: int = 100, slippage: int = 5) -> bool:
    try:
        print(f"Starting sell transaction for pool: {pool_str}")

        print("Fetching pool state...")
        pool_state: Optional[PoolState] = fetch_pool_state(pool_str)
        if pool_state is None:
            print("No pool state found, aborting transaction.")
            return False
        print("Pool state fetched successfully.")

        if pool_state.status != 0:
            print("This pool is no longer tradable on Launch Lab - it has migrated to Raydium CPMM...")
            return
        
        if pool_state.global_config != GLOBAL_CONFIG:
            print("Only Constant Product Curve is supported at this time...")
            return

        if not (1 <= percentage <= 100):
            print("Percentage must be between 1 and 100.")
            return False

        print("Retrieving token balance...")
        token_balance = get_token_balance(pool_state.base_mint)
        if token_balance is None or token_balance == 0:
            print("Token balance is zero. Nothing to sell.")
            return False

        print("Calculating transaction amounts...")
        sol_decimal = 1e9
        token_decimal = 10 ** pool_state.base_decimals
        slippage_adjustment = 1 - (slippage / 100)

        amount_in = int(token_balance * (percentage / 100))
        print(f"Base amount in (tokens): {amount_in / token_decimal}")

        if pool_state.platform_config == RAYDIUM_PLATFORM:
            protocol_fee_pct = 0.25
            platform_fee_pct = 0.75
        else:
            protocol_fee_pct = 0.25
            platform_fee_pct = 1  

        raw_amount_out = constant_product_sell_exact_in(
            pool_state.virtual_base,
            pool_state.virtual_quote,
            pool_state.real_base,
            pool_state.real_quote,
            amount_in,
            protocol_fee_pct,
            platform_fee_pct,
            0
        )

        min_amount_out = int(raw_amount_out * slippage_adjustment)
        print(f"Expected amount out (before slippage): {raw_amount_out / sol_decimal}")
        print(f"Minimum quote out (after {slippage}% slippage): {min_amount_out / sol_decimal}")

        print("Checking for associated token account...")
        token_account = get_associated_token_address(payer_keypair.pubkey(), pool_state.base_mint)

        print("Generating seed for WSOL account...")
        seed = base64.urlsafe_b64encode(os.urandom(24)).decode("utf-8")
        wsol_token_account = Pubkey.create_with_seed(payer_keypair.pubkey(), seed, TOKEN_PROGRAM_ID)
        balance_needed = Token.get_min_balance_rent_for_exempt_for_account(client)

        print("Creating and initializing WSOL account...")
        create_wsol_account_instruction = create_account_with_seed(
            CreateAccountWithSeedParams(
                from_pubkey=payer_keypair.pubkey(),
                to_pubkey=wsol_token_account,
                base=payer_keypair.pubkey(),
                seed=seed,
                lamports=int(balance_needed),
                space=ACCOUNT_SPACE,
                owner=TOKEN_PROGRAM_ID,
            )
        )

        init_wsol_account_instruction = initialize_account(
            InitializeAccountParams(
                program_id=TOKEN_PROGRAM_ID,
                account=wsol_token_account,
                mint=WSOL,
                owner=payer_keypair.pubkey(),
            )
        )

        print("Creating swap instruction...")
        accounts = [
            AccountMeta(payer_keypair.pubkey(), True, True),
            AccountMeta(AUTHORITY, False, False),
            AccountMeta(pool_state.global_config, False, False),
            AccountMeta(pool_state.platform_config, False, False),
            AccountMeta(pool_state.pool, False, True),
            AccountMeta(token_account, False, True),
            AccountMeta(wsol_token_account, False, True),
            AccountMeta(pool_state.base_vault, False, True),
            AccountMeta(pool_state.quote_vault, False, True),
            AccountMeta(pool_state.base_mint, False, False),
            AccountMeta(pool_state.quote_mint, False, False),
            AccountMeta(TOKEN_PROGRAM_ID, False, False),
            AccountMeta(TOKEN_PROGRAM_ID, False, False),
            AccountMeta(EVENT_AUTH, False, False),
            AccountMeta(PROGRAM_ID, False, False),
        ]

        data = bytearray()
        data.extend(bytes.fromhex("9527de9bd37c981a")) 
        data.extend(struct.pack('<Q', amount_in))
        data.extend(struct.pack('<Q', min_amount_out))
        data.extend(struct.pack('<Q', 0))
        swap_instruction = Instruction(PROGRAM_ID, bytes(data), accounts)

        print("Preparing to close WSOL account after swap...")
        close_wsol_account_instruction = close_account(
            CloseAccountParams(
                program_id=TOKEN_PROGRAM_ID,
                account=wsol_token_account,
                dest=payer_keypair.pubkey(),
                owner=payer_keypair.pubkey(),
            )
        )

        instructions = [
            set_compute_unit_limit(UNIT_BUDGET),
            set_compute_unit_price(UNIT_PRICE),
            create_wsol_account_instruction,
            init_wsol_account_instruction,
            swap_instruction,
            close_wsol_account_instruction,
        ]

        if percentage == 100:
            print("Preparing to close token account (100% sell)...")
            close_token_account_instruction = close_account(
                CloseAccountParams(
                    TOKEN_PROGRAM_ID,
                    token_account,
                    payer_keypair.pubkey(),
                    payer_keypair.pubkey(),
                )
            )
            instructions.append(close_token_account_instruction)

        print("Compiling transaction message...")
        compiled_message = MessageV0.try_compile(
            payer_keypair.pubkey(),
            instructions,
            [],
            client.get_latest_blockhash().value.blockhash,
        )

        print("Sending transaction...")
        txn_sig = client.send_transaction(
            txn=VersionedTransaction(compiled_message, [payer_keypair]),
            opts=TxOpts(skip_preflight=False)
        ).value
        print(f"Transaction Signature: {txn_sig}")

        print("Confirming transaction...")
        confirmed = confirm_txn(txn_sig)

        print(f"Transaction confirmed: {confirmed}")
        return confirmed
    
    except Exception as e:
        print("Error occurred during transaction:", e)
        return False