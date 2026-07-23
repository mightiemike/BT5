### Title
SwapAllowlistExtension Guard Bypassed via MetricOmmSimpleRouter — Any User Can Swap on Allowlisted Pools by Routing Through the Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router address (required for any router-mediated swap to work), every non-allowlisted user can bypass the guard by calling through the router.

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
``` [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [4](#0-3) 

The pool's `msg.sender` is now the **router address**, not the end user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`.

**The bypass path:**

1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to KYC'd users.
2. Admin allowlists specific user addresses: `setAllowedToSwap(pool, userA, true)`.
3. `userA` wants to use the router. Direct pool call works; router call fails because the extension sees `sender = router`, not `userA`.
4. Admin is forced to also allowlist the router: `setAllowedToSwap(pool, router, true)`.
5. Now `userB` (not allowlisted) calls `router.exactInputSingle(pool, ...)`. The pool sees `msg.sender = router`, the extension checks `allowedSwapper[pool][router] == true` → **passes**. `userB` bypasses the allowlist entirely.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap(...)` with the router as `msg.sender`. [5](#0-4) 

### Impact Explanation

Any user can bypass the `SwapAllowlistExtension` guard on a restricted pool by routing through `MetricOmmSimpleRouter`. The allowlist — intended to enforce per-user access control (e.g., KYC, compliance, private pools) — is rendered ineffective the moment the router is allowlisted. Non-allowlisted users gain full swap access to pools that were designed to exclude them, directly violating the pool admin's configured access policy and potentially causing fund-impacting consequences (e.g., regulatory exposure, unauthorized arbitrage against restricted liquidity).

### Likelihood Explanation

Medium. The trigger requires the pool admin to have allowlisted the router address, which is a natural operational step for any pool that wants to support router-mediated swaps for its allowlisted users. Once the router is allowlisted, the bypass is trivially reachable by any unprivileged user with no special setup.

### Recommendation

The `SwapAllowlistExtension` must gate the **end user**, not the intermediary. Two options:

1. **Pass the original caller through extension data**: The router encodes `msg.sender` into `extensionData`; the extension reads and verifies it. This requires a trusted router identity check (e.g., verify `msg.sender` of the pool call is a known factory-registered router before trusting the payload).

2. **Check `tx.origin` as a fallback for router calls**: Not recommended in general, but acceptable if the extension explicitly detects a trusted router and falls back to `tx.origin` for the identity check.

3. **Preferred — router-aware identity forwarding**: Add a standardized `swapperOverride` field to `extensionData` that the router populates with `msg.sender`. The extension checks: if `msg.sender` (the pool's caller) is a factory-registered router, use the override; otherwise use `sender` directly. This preserves the allowlist semantics for both direct and router-mediated swaps.

### Proof of Concept

```
Setup:
  pool = deploy pool with SwapAllowlistExtension
  admin.setAllowedToSwap(pool, userA, true)       // allowlist userA
  admin.setAllowedToSwap(pool, router, true)       // required for userA to use router

Attack:
  userB (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)
    → pool calls _beforeSwap(msg.sender=router, ...)
    → extension checks allowedSwapper[pool][router] == true → PASSES
    → userB's swap executes on the restricted pool

Result:
  userB bypasses the SwapAllowlistExtension guard with no privileged access.
``` [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```
