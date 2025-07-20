import math
from dataclasses import dataclass
from typing import Optional

from construct import Struct, Int8ul, Int64ul, Bytes, Array, Padding

from solders.pubkey import Pubkey  # type: ignore
from solana.rpc.commitment import Processed
from solana.rpc.types import MemcmpOpts

from config import client
from constants import WSOL, QUOTE_MINT, PROGRAM_ID

POOL_STATE_LAYOUT = Struct(
    Padding(8),
    "epoch" / Int64ul,
    "auth_bump" / Int8ul,
    "status" / Int8ul,
    "base_decimals" / Int8ul,
    "quote_decimals" / Int8ul,
    "migrate_type" / Int8ul,
    "supply" / Int64ul,
    "total_base_sell" / Int64ul,
    "virtual_base" / Int64ul,
    "virtual_quote" / Int64ul,
    "real_base" / Int64ul,
    "real_quote" / Int64ul,
    "total_quote_fund_raising" / Int64ul,
    "quote_protocol_fee" / Int64ul,
    "platform_fee" / Int64ul,
    "migrate_fee" / Int64ul,
    "vesting_total_locked_amount" / Int64ul,
    "vesting_cliff_period" / Int64ul,
    "vesting_unlock_period" / Int64ul,
    "vesting_start_time" / Int64ul,
    "vesting_allocated_share_amount" / Int64ul,
    "global_config" / Bytes(32),
    "platform_config" / Bytes(32),
    "base_mint" / Bytes(32),
    "quote_mint" / Bytes(32),
    "base_vault" / Bytes(32),
    "quote_vault" / Bytes(32),
    "creator" / Bytes(32),
    "padding" / Array(8, Int64ul)
)

@dataclass
class PoolState:
    pool: Pubkey
    epoch: int
    auth_bump: int
    status: int
    base_decimals: int
    quote_decimals: int
    migrate_type: int
    supply: int
    total_base_sell: int
    virtual_base: int
    virtual_quote: int
    real_base: int
    real_quote: int
    total_quote_fund_raising: int
    quote_protocol_fee: int
    platform_fee: int
    migrate_fee: int
    vesting_total_locked_amount: int
    vesting_cliff_period: int
    vesting_unlock_period: int
    vesting_start_time: int
    vesting_allocated_share_amount: int
    global_config: Pubkey
    platform_config: Pubkey
    base_mint: Pubkey
    quote_mint: Pubkey
    base_vault: Pubkey
    quote_vault: Pubkey
    creator: Pubkey

def fetch_pool_state(pool_str: str) -> Optional[PoolState]:
    try:
        pool_pubkey = Pubkey.from_string(pool_str)
        account_info = client.get_account_info(pool_pubkey, commitment=Processed)
        if not account_info.value or not account_info.value.data:
            return None

        decoded = POOL_STATE_LAYOUT.parse(account_info.value.data)

        return PoolState(
            pool=pool_pubkey,
            epoch=decoded.epoch,
            auth_bump=decoded.auth_bump,
            status=decoded.status,
            base_decimals=decoded.base_decimals,
            quote_decimals=decoded.quote_decimals,
            migrate_type=decoded.migrate_type,
            supply=decoded.supply,
            total_base_sell=decoded.total_base_sell,
            virtual_base=decoded.virtual_base,
            virtual_quote=decoded.virtual_quote,
            real_base=decoded.real_base,
            real_quote=decoded.real_quote,
            total_quote_fund_raising=decoded.total_quote_fund_raising,
            quote_protocol_fee=decoded.quote_protocol_fee,
            platform_fee=decoded.platform_fee,
            migrate_fee=decoded.migrate_fee,
            vesting_total_locked_amount=decoded.vesting_total_locked_amount,
            vesting_cliff_period=decoded.vesting_cliff_period,
            vesting_unlock_period=decoded.vesting_unlock_period,
            vesting_start_time=decoded.vesting_start_time,
            vesting_allocated_share_amount=decoded.vesting_allocated_share_amount,
            global_config=Pubkey.from_bytes(decoded.global_config),
            platform_config=Pubkey.from_bytes(decoded.platform_config),
            base_mint=Pubkey.from_bytes(decoded.base_mint),
            quote_mint=Pubkey.from_bytes(decoded.quote_mint),
            base_vault=Pubkey.from_bytes(decoded.base_vault),
            quote_vault=Pubkey.from_bytes(decoded.quote_vault),
            creator=Pubkey.from_bytes(decoded.creator),
        )

    except Exception as e:
        print(f"Error fetching pool state: {e}")
        return None

def fetch_pool_from_rpc(token_mint: str) -> str:
    memcmp_filter_base = MemcmpOpts(offset=205, bytes=token_mint)
    memcmp_filter_quote = MemcmpOpts(offset=237, bytes=QUOTE_MINT)

    try:
        print(f"Fetching Pool account for base_mint: {token_mint}, quote_mint: {QUOTE_MINT}")
        response = client.get_program_accounts(
            PROGRAM_ID,
            commitment=Processed,
            filters=[memcmp_filter_base, memcmp_filter_quote],
        )
        accounts = response.value
        if accounts:
            return str(accounts[0].pubkey)
    except Exception as e:
        print(f"Error fetching Pool account: {e}")
    
    return None

def get_pool_pda(base_mint_str: str) -> Pubkey:
    base_mint = Pubkey.from_string(base_mint_str)
    return str(Pubkey.find_program_address([b"pool", bytes(base_mint), bytes(WSOL)], PROGRAM_ID)[0])

def constant_product_buy_exact_in(
    virtual_base, virtual_quote, real_base, real_quote,
    amount_in,
    protocol_fee_pct=0.25,
    platform_fee_pct=1.0,
    share_fee_pct=0.0
):
    input_reserve = virtual_quote + real_quote
    output_reserve = virtual_base - real_base

    total_fee_pct = protocol_fee_pct + platform_fee_pct + share_fee_pct
    effective_input = int(amount_in * (1 - total_fee_pct / 100))

    return (effective_input * output_reserve) // (input_reserve + effective_input)

def constant_product_sell_exact_in(
    virtual_base, virtual_quote, real_base, real_quote,
    amount_in,
    protocol_fee_pct=0.25,
    platform_fee_pct=1.0,
    share_fee_pct=0.0
):
    input_reserve = virtual_base - real_base
    output_reserve = virtual_quote + real_quote

    gross_out = (amount_in * output_reserve) // (input_reserve + amount_in)

    protocol_fee = (gross_out * int(protocol_fee_pct * 100)) // 10000
    platform_fee = (gross_out * int(platform_fee_pct * 100)) // 10000
    share_fee = (gross_out * int(share_fee_pct * 100)) // 10000

    final_out = gross_out - protocol_fee - platform_fee - share_fee

    return final_out

### FUTURE USE - NOT SURE IF THE CALCULATIONS ARE CORRECT FOR FIXED AND LINEAR CURVES ###

def fixed_price_buy_exact_in(
    virtual_base, virtual_quote,
    amount_in,
    protocol_fee_pct=0.25,
    platform_fee_pct=1.0,
    share_fee_pct=0.0
):

    gross_out = (amount_in * virtual_base) // virtual_quote

    protocol_fee = (gross_out * int(protocol_fee_pct * 100)) // 10000
    platform_fee = (gross_out * int(platform_fee_pct * 100)) // 10000
    share_fee = (gross_out * int(share_fee_pct * 100)) // 10000

    return gross_out - protocol_fee - platform_fee - share_fee

def fixed_price_sell_exact_in(
    virtual_base, virtual_quote,
    amount_in,
    protocol_fee_pct=0.25,
    platform_fee_pct=1.0,
    share_fee_pct=0.0
):
    gross_out = (amount_in * virtual_quote) // virtual_base

    protocol_fee = (gross_out * int(protocol_fee_pct * 100)) // 10000
    platform_fee = (gross_out * int(platform_fee_pct * 100)) // 10000
    share_fee = (gross_out * int(share_fee_pct * 100)) // 10000

    return gross_out - protocol_fee - platform_fee - share_fee

def linear_price_buy_exact_in(
    virtual_base, real_base, real_quote,
    amount_in,
    protocol_fee_pct=0.25,
    platform_fee_pct=1.0,
    share_fee_pct=0.0
):
    Q64 = 1 << 64
    new_quote = real_quote + amount_in
    term_inside_sqrt = (2 * new_quote * Q64) // virtual_base
    sqrt_term = int(math.isqrt(term_inside_sqrt))
    gross_out = sqrt_term - real_base

    total_fee_pct = protocol_fee_pct + platform_fee_pct + share_fee_pct
    fee = (gross_out * total_fee_pct) // 100
    return gross_out - fee

def linear_price_sell_exact_in(
    virtual_base, real_base, real_quote,
    amount_in,
    protocol_fee_pct=0.25,
    platform_fee_pct=1.0,
    share_fee_pct=0.0
):
    Q64 = 1 << 64
    new_base = real_base - amount_in
    new_base_squared = new_base * new_base
    new_quote = (virtual_base * new_base_squared + (2 * Q64 - 1)) // (2 * Q64)
    gross_out = real_quote - new_quote

    total_fee_pct = protocol_fee_pct + platform_fee_pct + share_fee_pct
    fee = (gross_out * total_fee_pct) // 100
    return gross_out - fee
