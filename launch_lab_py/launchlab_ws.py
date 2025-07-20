from datetime import datetime
import json
import struct
import time

import base58
import websocket

from solana.rpc.api import Client
from solders.pubkey import Pubkey  # type: ignore
from solders.signature import Signature  # type: ignore

API_KEY = ""
WSS = "wss://mainnet.helius-rpc.com/?api-key=" + API_KEY
RPC = "https://mainnet.helius-rpc.com/?api-key=" + API_KEY
CLIENT = Client(RPC)

def decode_pool_create_event(hex_data: str):
    
    data = bytes.fromhex(hex_data)
    offset = 16

    def read_pubkey() -> str:
        nonlocal offset
        pk_bytes = data[offset:offset + 32]
        offset += 32
        return str(Pubkey.from_bytes(pk_bytes))

    def read_u8() -> int:
        nonlocal offset
        val = data[offset]
        offset += 1
        return val

    def read_u64() -> int:
        nonlocal offset
        (val,) = struct.unpack_from("<Q", data, offset)
        offset += 8
        return val

    def read_length_prefixed_string() -> str:
        nonlocal offset
        if offset + 4 > len(data):
            raise ValueError("Not enough data for string length")
        (length,) = struct.unpack_from("<I", data, offset)
        offset += 4
        if offset + length > len(data):
            raise ValueError(f"String length {length} exceeds buffer")
        raw = data[offset:offset + length]
        offset += length
        return raw.decode("utf-8", errors="replace")

    pool_state = read_pubkey()
    creator = read_pubkey()
    config = read_pubkey()

    decimals = read_u8()
    name = read_length_prefixed_string()
    symbol = read_length_prefixed_string()
    uri = read_length_prefixed_string()

    curve_variant = read_u8()

    curve_variants = {
        0: "Constant",
        1: "Fixed",
        2: "Linear"
    }
    
    variant = curve_variants.get(curve_variant, {})

    curve_supply = read_u64()
    curve_total_base_sell = read_u64()
    curve_total_quote_fund_raising = read_u64()
    curve_migrate_type = read_u8()

    total_locked_amount = read_u64()
    cliff_period = read_u64()
    unlock_period = read_u64()

    return {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        "pool_state": pool_state,
        "creator": creator,
        "config": config,
        "mint_params": {
            "decimals": decimals,
            "name": name,
            "symbol": symbol,
            "uri": uri,
        },
        "curve_params": {
            "variant": variant,
            "supply": curve_supply,
            "total_base_sell": curve_total_base_sell,
            "total_quote_fund_raising": curve_total_quote_fund_raising,
            "migrate_type": curve_migrate_type
        },
        "vesting_params": {
            "total_locked_amount": total_locked_amount,
            "cliff_period": cliff_period,
            "unlock_period": unlock_period,
        }
    }


def on_message(ws, message):
    try:
        payload = json.loads(message)
    except json.JSONDecodeError as e:
        print(f"JSON decode error: {e}")
        return

    result = payload.get("params", {}).get("result", {}).get("value", {})
    logs = result.get("logs", [])
    sig_str = result.get("signature")

    if not sig_str:
        return

    if not any(log.startswith("Program log: Instruction: InitializeMint2") for log in logs):
        return

    txn_sig = Signature.from_string(sig_str)
    print(f"Txn Sig: {txn_sig}")

    txn_data = get_txn(txn_sig=txn_sig)
    if not txn_data:
        return

    pool_create_event = None

    try:
        for inner_instruction in txn_data.get("innerInstructions", []):
            for instruction in inner_instruction.get("instructions", []):
                if len(instruction.get("accounts", [])) != 1:
                    continue
                try:
                    decoded = base58.b58decode(instruction["data"])
                    decoded_hex = decoded.hex()
                    if len(decoded_hex) > 200:
                        pool_create_event = decode_pool_create_event(decoded_hex)
                        if pool_create_event:
                            raise StopIteration  # exit both loops once we decode successfully
                except:
                    continue
    except StopIteration:
        pass

    if not pool_create_event:
        return

    mint = None
    for post_token_balance in txn_data.get("postTokenBalances", []):
        mint = post_token_balance.get("mint")
        if mint != "So11111111111111111111111111111111111111112":
            break

    if mint:
        pool_create_event = {"mint": mint, **pool_create_event}
        print(pool_create_event, "\n")



def on_error(ws, error):
    print(f"WebSocket error: {error}")


def on_close(ws, close_status_code, close_msg):
    print("WebSocket connection closed")


def on_open(ws):
    sub_req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "logsSubscribe",
        "params": [
            {"mentions": ["LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj"]},
            {"commitment": "confirmed"},
        ],
    }
    try:
        ws.send(json.dumps(sub_req))
        print("Subscribed to logs...")
    except Exception as e:
        print(f"Error sending subscription request: {e}")


def start_websocket():
    ws = websocket.WebSocketApp(
        WSS, on_message=on_message, on_error=on_error, on_close=on_close
    )
    ws.on_open = on_open
    ws.run_forever()

def get_txn(txn_sig: Signature, max_retries: int = 20, retry_interval: int = 3) -> bool:
    retries = 1
    
    while retries < max_retries:
        try:
            txn_res = CLIENT.get_transaction(txn_sig, encoding="json", commitment="confirmed", max_supported_transaction_version=0)
            txn_json = json.loads(txn_res.value.transaction.meta.to_json())
            
            if txn_json['err'] is None:
                return txn_json
            
            if txn_json['err']:
                return None
        except Exception as e:
            retries += 1
            time.sleep(retry_interval)
    
    return None

if __name__ == "__main__":
    try:
        start_websocket()
    except Exception as e:
        print(f"Unexpected error in main event loop: {e}")