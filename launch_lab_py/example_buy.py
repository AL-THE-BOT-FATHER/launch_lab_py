from launch_lab import buy
from pool_utils import get_pool_pda

mint_str = "launch_lab_address"
sol_in = .01
slippage = 5
pool_str = get_pool_pda(mint_str)
if pool_str:
    buy(pool_str, sol_in, slippage)
else:
    print("No pool account found...")
