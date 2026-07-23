### Title
SwapAllowlistExtension Bypass via MetricOmmSimpleRouter — Any Unprivileged User Can Swap on Allowlisted Pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router address to enable router-mediated swaps for their users, the allowlist is completely bypassed: any unprivileged user can swap on the restricted pool by calling the public router.

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is the pool's own `msg.sender` at the time `swap()` was called:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

The pool passes `msg.sender` as `sender` to the extension:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` as itself:

```solidity
// MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

The pool's `msg.sender` is the router. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. The actual user's identity is stored only in the transient callback context for token payment and is never forwarded to the extension.

A pool admin who wants to allow their allowlisted users to swap through the router must allowlist the router address. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for every caller of the router, regardless of who they are. The per-user allowlist is rendered inoperative.

The same identity mismatch applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

### Impact Explanation

**High**: Any user can bypass the swap allowlist on a pool that has allowlisted the router address. The pool was designed to restrict swaps to specific counterparties (e.g., a private institutional pool, a KYC-gated pool, or a pool with a curated LP set). Once bypassed, unauthorized users can execute swaps against LP assets, draining value from LPs who deposited under the assumption that only vetted counterparties would trade. The allowlist guard — the only access-control mechanism on the swap path — provides no protection.

### Likelihood Explanation

**Medium**: The pool admin must allowlist the router address. This is the natural and expected configuration for any pool that intends to allow its allowlisted users to swap through the standard periphery router. An admin who does not allowlist the router will find that even their explicitly allowlisted EOAs cannot use the router (the router's address fails the check), forcing them to allowlist the router. The misconfiguration is therefore a predictable consequence of the design, not an exotic mistake.

### Recommendation

The `SwapAllowlistExtension` must gate on the economically relevant actor, not the immediate `msg.sender` of `pool.swap()`. Two complementary fixes:

1. **Router-side**: `MetricOmmSimpleRouter` should forward the original `msg.sender` as an authenticated `sender` field in `extensionData`, and the extension should decode and verify it. This requires a trusted forwarding convention between the router and the extension.

2. **Extension-side**: Alternatively, the extension can accept a signed or otherwise authenticated user identity from `extensionData` and verify it against the allowlist, ignoring the raw `sender` argument when a trusted router is the caller.

Until fixed, pool admins should not use `SwapAllowlistExtension` with the public router if they intend per-user access control.

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `beforeSwap`.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed to swap.
3. Admin calls `setAllowedToSwap(pool, router, true)` — the router is allowlisted so Alice can use it.
4. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. The pool calls `extension.beforeSwap(sender=router, ...)`.
7. The extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob successfully swaps on the restricted pool, bypassing the allowlist entirely. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-175)
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
```
