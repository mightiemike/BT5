### Title
`SwapAllowlistExtension` gates on the router address instead of the original EOA, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the immediate caller of `pool.swap()` as the "swapper" identity. When swaps are routed through `MetricOmmSimpleRouter`, that immediate caller is the router contract, not the original EOA. If a pool admin allowlists the router address (a natural step to let allowlisted users access the router), every unpermissioned user can bypass the curated-pool gate by routing through the same public router.

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the allowlist check as:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is the first argument forwarded by the pool — which is `msg.sender` of the pool's own `swap` call:

```solidity
_beforeSwap(
    msg.sender,   // ← this becomes `sender` in the extension
    recipient,
    ...
``` [2](#0-1) 

When `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()`, the pool's `msg.sender` is the **router contract**, not the originating EOA:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
``` [3](#0-2) 

The router stores the original EOA only in transient storage for the payment callback; it is never forwarded to the pool or the extension. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalEOA]`.

A pool admin who wants allowlisted users to be able to use the router must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for **every** call that arrives through the router, regardless of who the originating EOA is. The individual per-user allowlist is completely bypassed.

The same structural problem applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all of them call `pool.swap()` with `msg.sender = router`. [4](#0-3) 

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., KYC-verified addresses, institutional partners) loses that restriction entirely once the router is allowlisted. Any unpermissioned EOA can call `router.exactInputSingle(pool, ...)` and execute swaps against the pool's LP positions. LPs deposited under the assumption that only vetted counterparties would trade against them; adverse-selection losses from unrestricted public flow are a direct loss of LP principal. This matches the "allowlist bypass" impact class: broken core pool functionality causing loss of LP funds.

### Likelihood Explanation

The trigger requires the pool admin to allowlist the router. This is a natural and expected operational step: without it, no allowlisted user can use the router either, making the router useless for that pool. The admin has no way to simultaneously allow allowlisted users to route through the router and block non-allowlisted users from doing the same, because the extension has no visibility into the originating EOA. The misconfiguration is therefore likely in any deployment that intends to support router-mediated swaps on a curated pool.

### Recommendation

1. **Document the invariant explicitly**: the router address must never be added to the swap allowlist if per-user access control is the goal; allowlisted users must call `pool.swap()` directly.
2. **Propagate the originating EOA**: have the router encode `msg.sender` into `extensionData` (or a dedicated field) so the extension can verify the true initiator. The extension would then decode and check the EOA rather than the `sender` argument.
3. **Alternatively**, add a dedicated `originalSender` field to the `beforeSwap` hook signature so the pool can forward the true initiator through the periphery chain without relying on `extensionData` encoding conventions.

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin allowlists only user A:
       swapExtension.setAllowedToSwap(pool, userA, true)
3. Pool admin also allowlists the router so userA can use it:
       swapExtension.setAllowedToSwap(pool, router, true)
4. Non-allowlisted userB calls:
       router.exactInputSingle({pool: pool, ...})
   → router calls pool.swap(); pool's msg.sender = router
   → _beforeSwap(sender=router, ...)
   → extension checks allowedSwapper[pool][router] → true
   → swap executes; userB trades against LP positions
5. userB, who was never individually allowlisted, has successfully
   bypassed the curated-pool gate and traded against LP funds.
``` [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L217-241)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
