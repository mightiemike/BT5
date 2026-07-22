### Title
`SwapAllowlistExtension` checks the router address instead of the end user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the router address, not the end user. If the pool admin allowlists the router (required for any user to use it), every user — including explicitly disallowed ones — can bypass the curated allowlist.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap()` receives `sender` from the pool and checks it against the per-pool allowlist:

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
``` [1](#0-0) 

The pool passes its own `msg.sender` as `sender` to `_beforeSwap`:

```solidity
// ExtensionCalling.sol L149-177 (simplified)
function _beforeSwap(address sender, ...) internal {
    _callExtensionsInOrder(BEFORE_SWAP_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...)));
}
``` [2](#0-1) 

And `MetricOmmPool.swap()` supplies `msg.sender` as that `sender`: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle()` is used, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [4](#0-3) 

The router does not forward the original user's address. Therefore `sender` arriving at the extension is the **router address**, not the end user. The allowlist check becomes `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`.

The same bypass applies to `exactOutputSingle`, `exactInput`, and `exactOutput`. [5](#0-4) 

By contrast, `DepositAllowlistExtension.beforeAddLiquidity()` correctly checks `owner` (the economically relevant actor who receives LP shares), not `sender` (the caller): [6](#0-5) 

This asymmetry confirms the swap allowlist is checking the wrong actor.

---

### Impact Explanation

A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., KYC'd counterparties, protocol-owned addresses, or whitelisted market makers). The admin must allowlist the router for any allowed user to trade through the standard periphery. Once the router is allowlisted, every address — including explicitly disallowed ones — can call `router.exactInputSingle()` and trade freely. The allowlist provides zero protection against router-mediated swaps. Disallowed users can drain LP value from a pool that was designed to only accept trusted counterparties.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, documented swap entrypoint. Any user who discovers the allowlist blocks their direct `pool.swap()` call will naturally try the router. No special privileges, flash loans, or contract deployment are required — a single `exactInputSingle` call suffices. The pool admin's only mitigation would be to not allowlist the router at all, which breaks the pool for all users.

---

### Recommendation

Pass the original end-user address through the call chain so the extension can check the economically relevant actor. One approach: add a `swapper` parameter to `pool.swap()` that the router populates with `msg.sender` before calling the pool, and have the pool forward that address as `sender` to extensions. Alternatively, mirror the deposit allowlist pattern and check a caller-supplied identity that the pool validates (e.g., via a signed permit). At minimum, document that allowlisting the router opens the allowlist to all users, and provide a router-aware allowlist variant that reads the original initiator from transient storage.

---

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Admin allowlists alice (allowed user) and the router address.
3. Admin does NOT allowlist bob (disallowed user).
4. bob calls pool.swap() directly → reverts NotAllowedToSwap (sender=bob, not in allowlist).
5. bob calls router.exactInputSingle({pool: pool, ...}) → router calls pool.swap() with msg.sender=router.
6. Extension checks allowedSwapper[pool][router] → true (router is allowlisted).
7. Swap succeeds. bob trades on the curated pool, bypassing the intended access control.
```

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

**File:** metric-core/contracts/MetricOmmPool.sol (L281-295)
```text
    _afterSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      packedSlot0Final,
      bidPriceX64,
      askPriceX64,
      amount0Delta.toInt128(),
      amount1Delta.toInt128(),
      protocolFeeAmount,
      extensionData
    );
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
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
