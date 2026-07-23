Audit Report

## Title
Router address substitutes end-user identity in `SwapAllowlistExtension::beforeSwap`, enabling allowlist bypass for any unprivileged caller — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the value passed from `MetricOmmPool::swap()` as `msg.sender`. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool boundary is the router contract, not the end-user. Any user not individually allowlisted can bypass the restriction by calling the router, provided the router itself is allowlisted — which is the only way to enable router-mediated swaps for any permitted user.

## Finding Description

**Confirmed call chain (all code verified in repository):**

`MetricOmmSimpleRouter::exactInputSingle()` calls `pool.swap()` directly with no user identity forwarded: [1](#0-0) 

`MetricOmmPool::swap()` passes `msg.sender` (the router) as `sender` to `_beforeSwap`: [2](#0-1) 

`ExtensionCalling::_beforeSwap()` forwards `sender` (router address) verbatim to the extension with no transformation: [3](#0-2) 

`SwapAllowlistExtension::beforeSwap()` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the router — the end-user address is never present in this check: [4](#0-3) 

The `onlyPool` modifier in `BaseMetricExtension` confirms `msg.sender` to the extension is always the pool, not the user: [5](#0-4) 

**Root cause:** The pool has no mechanism to distinguish the initiating user from the immediate caller. The router stores the payer in transient storage for callback purposes (`_setNextCallbackContext`) but never communicates the end-user identity to the pool or extension layer.

**Two broken states:**

| Router allowlisted? | Effect |
|---|---|
| Yes | Every user — including those not individually allowlisted — can swap through the router. Per-user allowlist fully bypassed. |
| No | Individually allowlisted users cannot use the router at all. Core router functionality broken for permitted users. |

There is no configuration that simultaneously allows specific users through the router and blocks others.

## Impact Explanation

Broken core pool functionality and admin-boundary break. `SwapAllowlistExtension` is the designated per-user swap access control mechanism. When the router is allowlisted (the only viable configuration for router-mediated swaps), the allowlist ceases to gate individual swappers. Any unprivileged user can execute swaps on a pool the admin intended to restrict, with no additional preconditions. This constitutes unauthorized swap execution on a restricted pool.

## Likelihood Explanation

The router is the primary public entrypoint for swaps. Any pool that (a) uses `SwapAllowlistExtension` and (b) allowlists the router to enable router-mediated swaps for its permitted users is immediately and fully vulnerable. No special attacker capability is required — only calling `exactInputSingle`, `exactOutputSingle`, `exactInput`, or `exactOutput` through the public router. The condition is a natural and expected operational state.

## Recommendation

Pass the original caller's identity through the extension data or a dedicated field. Concrete options:

1. Have the router encode `msg.sender` (end-user) into `extensionData` before calling `pool.swap()`, and have `SwapAllowlistExtension::beforeSwap` decode and check that address when `sender` is a known router.
2. Maintain a router registry in the extension; when `sender` is a registered router, decode the true initiator from `extensionData` and check `allowedSwapper[pool][trueInitiator]`.
3. Use transient storage in the pool to record the true initiator before calling hooks, and expose it to extensions.

## Proof of Concept

```
Setup:
  allowAllSwappers[pool] = false
  allowedSwapper[pool][router] = true    // admin enables router swaps
  allowedSwapper[pool][alice] = true     // alice is individually permitted
  allowedSwapper[pool][attacker] = false // attacker is NOT permitted

Direct swap by attacker → reverts (allowedSwapper[pool][attacker] = false) ✓

Router swap by attacker:
  router.exactInputSingle(ExactInputSingleParams({ pool: pool, ... }))
  → pool.swap() called with msg.sender = router
  → _beforeSwap(sender=router, ...)
  → allowedSwapper[pool][router] = true → PASSES
  → Attacker executes swap successfully ← BYPASS ✗
```

The `sender` checked by the hook is the router address, not the attacker. The allowlist is bypassed for any caller routing through `MetricOmmSimpleRouter`. [6](#0-5)

### Citations

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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
  }
```
