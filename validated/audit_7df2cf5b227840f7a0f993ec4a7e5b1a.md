Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of originating user, allowing any router caller to bypass the swap allowlist - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool, which is always `msg.sender` of the `pool.swap(...)` call. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router to enable approved users to reach the pool through the periphery, every public caller of the router inherits the router's allowlisted status, rendering the allowlist gate entirely ineffective for router-mediated swaps.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` verbatim as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol lines 230-240
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap
    recipient, zeroForOne, amountSpecified, priceLimitX64,
    packedSlot0Initial, bidPriceX64, askPriceX64, extensionData
);
``` [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` checks that value against its per-pool allowlist, where `msg.sender` is the pool and `sender` is the address the pool forwarded:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router the `msg.sender` of that call. The original caller's address is stored only in transient callback context for token settlement and is never forwarded to the pool as the `sender` identity:

```solidity
// MetricOmmSimpleRouter.sol lines 71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [3](#0-2) 

The same pattern applies to `exactOutputSingle` and all multi-hop paths (`exactInput`, `exactOutput`). [4](#0-3) 

**Exploit path:**
1. Pool admin deploys pool with `SwapAllowlistExtension`, sets `allowAllSwappers[pool] = false`, and allowlists a set of approved users.
2. To let approved users reach the pool through the periphery, admin calls `setAllowedToSwap(pool, router, true)`.
3. Any unapproved user calls `router.exactInputSingle(...)` targeting the restricted pool.
4. The pool calls `_beforeSwap(msg.sender=router, ...)`. The extension checks `allowedSwapper[pool][router]` → `true` → passes.
5. The unapproved user's swap executes against the restricted pool.

## Impact Explanation
`SwapAllowlistExtension` is the sole on-chain mechanism for restricting swap access on a pool. Once bypassed via the router, every public caller of `MetricOmmSimpleRouter` can execute swaps that the pool admin explicitly intended to block. This constitutes broken core pool functionality: the allowlist gate is rendered completely ineffective for all router-mediated swaps, breaking the core access-control invariant of the extension.

## Likelihood Explanation
The bypass requires the router to be allowlisted, which is the natural and expected operational configuration. Without allowlisting the router, even legitimately approved users cannot reach the pool through the standard periphery, making the allowlist operationally unusable for any real deployment. A pool admin reading the `SwapAllowlistExtension` interface in isolation has no indication that allowlisting the router grants access to all router users rather than only the approved subset. The likelihood of this misconfiguration is high for any pool using both the allowlist extension and the standard router.

## Recommendation
The extension must gate the economically relevant actor — the end user — not the intermediary contract. Two viable approaches:

1. **Pass originating user in `extensionData`**: Have the router encode `msg.sender` into `extensionData` and have the extension decode and check it. This requires a coordinated convention between router and extension.
2. **Reject router-mediated calls unless authenticated**: The extension can detect when `sender` is a known router and revert unless `extensionData` carries a verifiable user identity (e.g., signed by a trusted relayer or the router itself via a dedicated encoding scheme).

## Proof of Concept
```solidity
// 1. Deploy pool with SwapAllowlistExtension
// 2. setAllowAllSwappers(pool, false)
// 3. setAllowedToSwap(pool, approvedUser, true)
// 4. setAllowedToSwap(pool, address(router), true)  // natural config for periphery access
// 5. As an unapproved attacker:
router.exactInputSingle(ExactInputSingleParams({
    pool: restrictedPool,
    recipient: attacker,
    zeroForOne: true,
    amountIn: 1e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    tokenIn: token0,
    extensionData: ""
}));
// Pool calls _beforeSwap(msg.sender=router, ...)
// Extension checks allowedSwapper[pool][router] → true → swap succeeds
// Attacker bypasses allowlist
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-137)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```
