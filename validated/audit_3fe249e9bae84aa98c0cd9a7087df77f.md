Looking at the code carefully across all three files to validate the claim.

Audit Report

## Title
Missing Deadline in `addLiquidityExactShares` Allows Stale-Price Liquidity Deposits — (`metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

## Summary
`MetricOmmPoolLiquidityAdder` exposes `addLiquidityExactShares` (both overloads) and `addLiquidityWeighted` (both overloads) with no `deadline` parameter and no `_checkDeadline` call, while `MetricOmmSimpleRouter` enforces `_checkDeadline` on every swap entry point. A pending transaction that executes after the pool price has moved forces the LP to deposit at a price they never accepted, causing immediate impermanent loss bounded only by the caller-supplied token caps.

## Finding Description
`MetricOmmSwapRouterBase._checkDeadline` (line 91–94) reverts when `block.timestamp > deadline`. Every swap entry point in `MetricOmmSimpleRouter` — `exactInputSingle` (line 68), `exactInput` (line 93), `exactOutputSingle` (line 131), `exactOutput` (line 155) — calls `_checkDeadline` as its first action.

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` (lines 56–81) accepts `pool`, `owner`, `salt`, `deltas`, `maxAmountToken0`, `maxAmountToken1`, and `extensionData` — no `deadline`. The only guards are `_validateOwner`, `_validateDeltas`, and the token-cap check inside `metricOmmModifyLiquidityCallback` (line 165). Token caps limit absolute spend but do not prevent execution at a stale price ratio.

`addLiquidityWeighted` (lines 88–149) adds `_validateBinAndBinPosition` (lines 263–286), which checks the pool cursor against caller-supplied `[minimalCurBin, maximalCurBin]` bounds. This is a partial mitigation: it reverts only if the cursor has moved outside the specified bin range. Within a bin, or when the caller supplies wide bounds, significant price movement can occur without triggering a revert. No deadline is checked.

Exploit path for `addLiquidityExactShares`:
1. LP calls `addLiquidityExactShares` with shares calibrated to the current pool price and caps set to the expected token amounts.
2. Transaction enters the mempool. A validator delays inclusion (e.g., during congestion or MEV reordering).
3. Pool price moves — oracle update shifts the cursor or bin prices change.
4. Transaction executes. `_addLiquidity` calls `pool.addLiquidity`; the pool computes token amounts from the new price state. The callback enforces only `amount0Delta <= max0 && amount1Delta <= max1` (line 165).
5. LP deposits at the new price ratio. The deposited value is less than the market value of the tokens at the price the LP intended, constituting immediate impermanent loss.

## Impact Explanation
Direct loss of LP principal. The LP deposits tokens at a price they did not accept; the difference between the intended deposit value and the actual deposit value at the stale price is an immediate, unrecoverable loss. The loss is bounded by the token caps but can be material in volatile markets or during periods of mempool congestion. This matches the "direct loss of user principal" allowed impact.

## Likelihood Explanation
Any unprivileged LP calling `addLiquidityExactShares` is exposed. No special attacker capability is required beyond the ability to delay transaction inclusion (standard mempool dynamics, network congestion, or MEV). The condition is repeatable on every deposit. `addLiquidityWeighted` is partially mitigated by cursor bounds but remains exposed within a bin or with wide bounds.

## Recommendation
Add a `uint256 deadline` parameter to all four public entry points and call `_checkDeadline(deadline)` as the first statement, matching the pattern already established in `MetricOmmSwapRouterBase`. For `addLiquidityExactShares`, also consider adding optional cursor-bounds validation analogous to `_validateBinAndBinPosition` in `addLiquidityWeighted`.

## Proof of Concept
```solidity
// Foundry fork test sketch
function test_stalePrice_addLiquidityExactShares() public {
    // 1. Snapshot pool price at block N
    uint256 price0 = pool.slot0().curPosInBin; // record current price

    // 2. Warp time / advance blocks to simulate mempool delay
    vm.warp(block.timestamp + 300);

    // 3. Simulate oracle/price update that shifts pool cursor
    // (e.g., via a swap that moves the cursor to a new bin)
    router.exactInputSingle(...); // moves pool price

    // 4. Execute the LP's original addLiquidityExactShares call
    // (no deadline → does not revert despite price change)
    (uint256 a0, uint256 a1) = adder.addLiquidityExactShares(
        pool, owner, salt, deltas, maxToken0, maxToken1, ""
    );

    // 5. Assert LP deposited at worse ratio than intended
    // a0/a1 ratio differs from price0, confirming stale-price deposit
    assertNotEq(a0 * price0, a1 * 1e18); // ratio mismatch = impermanent loss
}
```