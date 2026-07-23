Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension` is designed to gate swaps on curated pools by swapper identity. However, `MetricOmmPool.swap` passes `msg.sender` — the direct caller — as the `sender` argument to every extension hook. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the end user. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted. If the pool admin allowlists the router (the natural action to enable router-mediated swaps for their curated users), any unprivileged user can bypass the allowlist entirely by routing through the router.

## Finding Description

**Root cause — pool passes `msg.sender` (router) as `sender` to extensions.**

`MetricOmmPool.swap` calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` then forwards this `sender` value directly into the extension call: [2](#0-1) 

**`MetricOmmSimpleRouter` calls `pool.swap` directly, making itself `msg.sender`.**

`exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(...)` with no mechanism to forward the original caller's address: [3](#0-2) 

**`SwapAllowlistExtension.beforeSwap` checks `sender` (the router) against the allowlist.**

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` = pool, `sender` = router. The check resolves to `allowedSwapper[pool][router]`. [4](#0-3) 

**The two broken states this creates:**

| Router allowlisted? | Effect |
|---|---|
| **Yes** | Any user bypasses the allowlist by routing through the router |
| **No** | Allowlisted users cannot use the router at all (broken core flow) |

Neither state matches the intended semantics of "gate by actual swapper identity."

## Impact Explanation
A pool admin who deploys a curated pool with `SwapAllowlistExtension` and allowlists the router (to enable router-mediated swaps for their authorized users) inadvertently opens the pool to all users. Any unprivileged address can call `router.exactInputSingle(...)` targeting the pool and the extension will pass because `allowedSwapper[pool][router] == true`. Unauthorized traders can drain LP value through arbitrage or directional trading that the allowlist was meant to prevent. This constitutes broken core pool functionality causing loss of funds and an admin-boundary break where the pool admin's access control is bypassed by an unprivileged path.

## Likelihood Explanation
High. `MetricOmmSimpleRouter` is the primary public swap interface. Pool admins who configure a `SwapAllowlistExtension` will naturally need to decide whether to allowlist the router. The protocol provides no warning that allowlisting the router grants access to all users. The broken-functionality path (router not allowlisted, allowlisted users blocked) is the default state and affects every curated pool that uses this extension with the router.

## Recommendation
The pool should forward the original transaction origin or the router should encode the real user identity in a verifiable way. Two concrete options:

1. **Pass the real user address through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted router assumption.
2. **Add an `originSender` field to the swap hook signature:** The pool passes both `msg.sender` (direct caller) and a separately tracked origin address to extensions, allowing extensions to choose which identity to gate on.

## Proof of Concept
```
1. Pool admin deploys pool with SwapAllowlistExtension configured.
2. Pool admin calls:
       extension.setAllowedToSwap(pool, router, true)
   (intending to allow router-mediated swaps for their curated users)
3. Attacker (not in allowlist) calls:
       router.exactInputSingle({pool: pool, ...})
4. Router calls pool.swap(recipient, ...) → pool sees msg.sender = router
5. Pool calls extension.beforeSwap(sender=router, ...)
6. Extension evaluates: allowedSwapper[pool][router] == true → passes
7. Attacker's swap executes on the curated pool, bypassing the allowlist.
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
