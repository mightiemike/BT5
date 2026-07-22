### Title
`SwapAllowlistExtension` gates on the router's address instead of the end user, allowing any user to bypass per-user swap restrictions via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the actual end user. If the pool admin allowlists the router to enable router-based swaps, every user — including non-allowlisted ones — can bypass the per-user swap restriction.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it to every configured extension:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // <-- this is the router address when called via router
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the immediate caller of the pool:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle(...)`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

So `msg.sender` of the pool's `swap()` is the router address. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

A pool admin who wants to support router-based swaps must allowlist the router address. But doing so makes the allowlist check pass for **any** user who routes through the router, regardless of whether that user is individually allowlisted. The two goals — "allow router-based swaps" and "restrict swaps to specific users" — are mutually exclusive under the current design.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` in `MetricOmmSimpleRouter`, since all of them call `pool.swap(...)` with the router as `msg.sender`.

### Impact Explanation

A non-allowlisted user can trade on a curated pool that was intended to restrict access to specific addresses (e.g., KYC'd counterparties, whitelisted market makers). The unauthorized user can execute swaps against LP funds at oracle-derived prices, extracting value from the pool in ways the pool admin explicitly intended to prevent. This breaks the core access-control invariant of curated pools and constitutes a direct loss of LP principal or owed fees above contest thresholds.

### Likelihood Explanation

The trigger requires the pool admin to allowlist the router address, which is the natural and expected configuration for any curated pool that wants to support the standard periphery swap path. A pool admin who deploys a `SwapAllowlistExtension`-gated pool and then allowlists the router to enable user-friendly routing will unknowingly open the gate to all users. The attacker needs no special privileges — any EOA can call `MetricOmmSimpleRouter.exactInputSingle` with the target pool.

### Recommendation

The `SwapAllowlistExtension` should gate on the **end user** rather than the immediate pool caller. One approach is to pass the original `msg.sender` through `extensionData` from the router, and have the extension decode and verify it. A cleaner approach is to have the router forward the original caller's identity as a dedicated field, or to have the pool expose a transient "original sender" that extensions can read. Alternatively, the extension can maintain a separate allowlist for trusted intermediaries (like the router) that are permitted to forward swaps on behalf of any user, distinct from the per-user allowlist.

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin allowlists Alice: `swapExtension.setAllowedToSwap(pool, alice, true)`.
3. Pool admin allowlists the router to enable router-based swaps: `swapExtension.setAllowedToSwap(pool, address(router), true)`.
4. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, recipient: bob, ...})`.
5. Router calls `pool.swap(bob, ...)` with `msg.sender = router`.
6. `SwapAllowlistExtension.beforeSwap(sender=router, ...)` checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. Bob successfully swaps on the curated pool despite never being individually allowlisted.

Key code references: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
```
