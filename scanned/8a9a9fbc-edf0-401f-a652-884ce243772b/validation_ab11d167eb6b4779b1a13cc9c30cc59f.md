### Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the value the pool passes as the first argument to the hook — which is `msg.sender` of the `pool.swap(...)` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` seen by the pool is the **router**, not the end user. If the pool admin allowlists the router (the natural step to enable router-mediated swaps for their permitted users), every unpermissioned user can bypass the allowlist by simply calling the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` is:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the only caller allowed by `onlyPool`). `sender` is the first argument the pool passes to `_beforeSwap`, which is `msg.sender` of the pool's own `swap(...)` call. [1](#0-0) 

`ExtensionCalling._beforeSwap` is called from the pool with `sender` = the pool's `msg.sender`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly — the pool sees `msg.sender = router`: [3](#0-2) 

The router stores the actual end-user address only in transient storage for the payment callback — it is **never forwarded** to the pool as `sender`: [4](#0-3) 

So the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

**The broken invariant (analog to the external bug):** In the external report, `updateReward()` was applied on the inner function `_claimReward()` instead of only on the outer `fullyClaimReward()`, causing the guard to fire at the wrong level. Here, the allowlist guard fires against the wrong identity — the intermediary router rather than the actual economic actor — because the hook is wired to `sender` (pool's `msg.sender`) rather than the true originator.

---

### Impact Explanation

A pool admin who wants to restrict swaps to a specific set of addresses will naturally also allowlist `MetricOmmSimpleRouter` so that their permitted users can trade through the standard router. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for **every** caller of the router, regardless of whether the actual end user is on the allowlist. The allowlist is completely defeated for all router-mediated swaps. Any unpermissioned address can swap in a pool that was intended to be restricted.

---

### Likelihood Explanation

The scenario is reachable whenever:
1. A pool is deployed with `SwapAllowlistExtension` to restrict swappers.
2. The pool admin allowlists `MetricOmmSimpleRouter` so that their permitted users can use the standard periphery (a natural and expected operational step).
3. Any unpermissioned user calls `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` on the router targeting that pool.

No special privileges, no malicious setup, no non-standard tokens required. The trigger is a normal public router call.

---

### Recommendation

The pool's `swap` function should accept an explicit `sender` parameter from the caller (or the router should forward the actual originator via `extensionData`). Alternatively, `SwapAllowlistExtension.beforeSwap` should decode the true originator from `extensionData` when `sender` is a known router, or the router should pass `msg.sender` (the end user) as an authenticated field the extension can verify. The simplest fix is for the pool to expose a `swap` overload that accepts an explicit `effectiveSender` and validates it against `msg.sender` via a whitelist of trusted forwarders.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension`.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only `alice` should be able to swap.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — so `alice` can use the router.
4. `bob` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. The pool calls `_beforeSwap(sender=router, ...)`.
7. `SwapAllowlistExtension` evaluates `allowedSwapper[pool][router]` = `true` → passes.
8. `bob`'s swap executes successfully despite not being on the allowlist. [5](#0-4) [6](#0-5)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
