from launch_lab import sell
from pool_utils import get_pool_pda

mint_str = "launch_lab_address"
percentage = 100
slippage = 5
pool_str = get_pool_pda(mint_str)
if pool_str:
    sell(pool_str, percentage, slippage)
else:
    print("No pool account found...")
