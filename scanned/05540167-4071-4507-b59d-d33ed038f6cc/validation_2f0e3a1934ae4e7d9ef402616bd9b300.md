### Title
SwapAllowlistExtension gates the router address instead of the originating swapper, enabling full allowlist bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the direct `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool sees `msg.sender = router`, so the extension checks `allowedSwapper[pool][router]` — not the originating user. If the pool admin allowlists the router (the natural step to enable router-mediated swaps for their curated users), every unprivileged user can bypass the allowlist by routing through the shared public router contract.

---

### Finding Description

**Root cause — wrong actor bound in `beforeSwap`:**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

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

`msg.sender` is the pool (correct). `sender` is whatever the pool passes as the first argument to `IMetricOmmExtensions.beforeSwap`. That value is forwarded verbatim from `ExtensionCalling._beforeSwap`:

```solidity
// ExtensionCalling.sol L149-177
function _beforeSwap(address sender, ...) internal {
    _callExtensionsInOrder(
        BEFORE_SWAP_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeSwap,
            (sender, recipient, zeroForOne, amountSpecified, priceLimitX64,
             packedSlot0Initial, bidPriceX64, askPriceX64, extensionData))
    );
}
```

The pool calls `_beforeSwap(msg.sender, ...)`, so `sender` = whoever called `pool.swap()`.

**Router path — sender becomes the router:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
);
```

There is no mechanism to forward the original `msg.sender` (the end user) into the pool call. The pool therefore sees `msg.sender = MetricOmmSimpleRouter`, and the extension checks `allowedSwapper[pool][MetricOmmSimpleRouter]`.

**Bypass path:**

A pool admin who wants curated users to be able to swap through the standard router must allowlist the router address. Once `allowedSwapper[pool][router] = true`, every unprivileged caller — including addresses the admin never intended to allow — can bypass the allowlist by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutputSingle` / `exactOutput`). The router is a shared, permissionless public contract; allowlisting it is equivalent to disabling the allowlist for all router-mediated swaps.

The `DepositAllowlistExtension` does not share this flaw: it checks `owner` (the position owner explicitly passed through the call chain), which correctly identifies the economically relevant depositor regardless of routing.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict swaps to KYC'd or otherwise vetted addresses loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Unauthorized users can:

- Execute swaps on pools whose liquidity was provisioned exclusively for a restricted set of counterparties.
- Drain favorable-rate liquidity that LP providers deposited under the assumption that only vetted users would access it.

This constitutes a **curation failure with direct LP-asset loss**: LP providers cannot recover tokens already swapped out by unauthorized users at the pool's oracle-anchored rates.

---

### Likelihood Explanation

The trigger is a realistic, non-malicious admin action. Because direct `pool.swap()` calls require the caller to implement `metricOmmSwapCallback`, allowlisted users who are ordinary EOAs or simple contracts cannot use the pool without the router. The admin's only practical option to enable router usage is to allowlist the router address — at which point the bypass is immediately available to every user. No attacker key or privileged capability is required beyond the admin having taken this configuration step.

---

### Recommendation

Pass the originating user's identity through the swap call so the extension can gate the correct actor. Two concrete approaches:

1. **Add a `swapper` field to the swap parameters** that the router populates with `msg.sender` before calling the pool, and have the pool forward it as `sender` to `_beforeSwap` instead of its own `msg.sender`.

2. **Check `recipient` instead of `sender`** if the protocol's intent is to gate who receives the output (though this changes semantics). More correctly, introduce a dedicated `swapper` address in `IMetricOmmPoolActions.swap` that the router sets to `msg.sender` and the pool passes to extensions.

Until fixed, pool admins should be warned that allowlisting the router address disables the swap allowlist for all router-mediated swaps.

---

### Proof of Concept

```
1. Deploy a pool with SwapAllowlistExtension configured as beforeSwap hook.
2. Admin calls setAllowedToSwap(pool, alice, true)  — only alice is allowed.
3. Admin calls setAllowedToSwap(pool, router, true) — necessary so alice can use the router.
4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool, ...}).
5. Router calls pool.swap(); pool sees msg.sender = router.
6. Extension checks allowedSwapper[pool][router] → true → swap proceeds.
7. Bob successfully swaps on a pool he was never authorized to access.
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
