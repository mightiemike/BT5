### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When `MetricOmmSimpleRouter` mediates a swap, `msg.sender` inside `MetricOmmPool.swap` is the router contract, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`. A pool admin who allowlists the router to support router-mediated swaps for their curated users simultaneously opens the allowlist to every user on-chain.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
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

`SwapAllowlistExtension.beforeSwap` then checks that `sender` (the first argument) is allowlisted for the calling pool (`msg.sender` inside the extension = the pool):

```solidity
// SwapAllowlistExtension.sol line 31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant) is used, the call chain is:

```
user → MetricOmmSimpleRouter.exactInputSingle
         → IMetricOmmPoolActions(pool).swap(recipient, ...)
              msg.sender inside pool = MetricOmmSimpleRouter address
              → _beforeSwap(sender = MetricOmmSimpleRouter, ...)
                   → SwapAllowlistExtension.beforeSwap(sender = MetricOmmSimpleRouter, ...)
                        checks allowedSwapper[pool][MetricOmmSimpleRouter]
```

The router never forwards the original `msg.sender` (the actual end user) to the pool. There is no mechanism in the current interface to do so. The pool's `swap` signature accepts only `recipient`, not an explicit `swapper` identity.

A pool admin who wants allowlisted users to be able to use the router must add the router to `allowedSwapper[pool]`. Once the router is allowlisted, **any** address can call `MetricOmmSimpleRouter.exactInputSingle` and the extension will approve the swap, because the check resolves to `allowedSwapper[pool][router] == true` regardless of who the actual caller is.

The same structural issue applies to `exactInput`, `exactOutputSingle`, and `exactOutput`, all of which call `pool.swap` with `msg.sender = router`.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-gated, institutional, or whitelist-only pools) loses that protection entirely for any user who routes through `MetricOmmSimpleRouter`. The disallowed user receives pool output tokens at the oracle-derived price, draining LP-owned assets in a pool that was explicitly designed to exclude them. The allowlist guard — the sole access-control mechanism on the swap path — fails open for the entire public router surface.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical periphery swap entry point. Any pool admin who deploys a curated pool and wants their allowlisted users to be able to use the standard router (rather than calling the pool directly) must allowlist the router. This is a natural and expected configuration. Once that single admin action is taken, the bypass is unconditionally available to every on-chain address with no further preconditions.

---

### Recommendation

The `sender` identity checked by `SwapAllowlistExtension` must be the economic actor, not the immediate `msg.sender` of `pool.swap`. Two complementary fixes:

1. **Pool-level**: Add an explicit `swapper` parameter to `IMetricOmmPoolActions.swap` (distinct from `recipient`) that the pool passes to `_beforeSwap` as the identity to gate. The router would forward `msg.sender` as `swapper`.
2. **Extension-level (interim)**: Until the pool interface is updated, `SwapAllowlistExtension` can reject calls where `sender` is a known router/multicall contract, or the pool admin must be documented never to allowlist the router and instead require direct pool calls for curated pools.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is the intended curated user
  allowedSwapper[pool][router] = true         // admin adds router so alice can use it
  allowedSwapper[pool][charlie] = false       // charlie is explicitly excluded

Attack:
  charlie calls MetricOmmSimpleRouter.exactInputSingle({
      pool: pool,
      recipient: charlie,
      zeroForOne: true,
      amountIn: X,
      ...
  })

  → router calls pool.swap(charlie, true, X, ...)
       msg.sender inside pool = router
  → pool calls _beforeSwap(sender=router, ...)
  → SwapAllowlistExtension.beforeSwap(sender=router, ...)
       checks allowedSwapper[pool][router] == true  ✓
  → swap executes; charlie receives pool output tokens

Result: charlie, an explicitly disallowed address, successfully swaps
        against the curated pool, bypassing the allowlist guard entirely.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
