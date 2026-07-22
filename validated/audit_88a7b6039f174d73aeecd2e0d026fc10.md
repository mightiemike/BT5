### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Original User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `sender` is the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the originating EOA. If the router is allowlisted (a natural admin action to let allowlisted users access the router), every non-allowlisted user can bypass the swap gate by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` (the direct pool caller) is allowlisted for the calling pool (`msg.sender` inside the extension = the pool): [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant), the router itself calls `pool.swap(...)`: [3](#0-2) 

So the pool sees `msg.sender = router`, passes `sender = router` to the extension, and the extension evaluates `allowedSwapper[pool][router]` — **not** `allowedSwapper[pool][originalUser]`. The original user's allowlist entry is never consulted.

The `DepositAllowlistExtension` does **not** share this flaw: it ignores the `sender` parameter entirely and gates on `owner` (the position owner), which is the economically relevant identity regardless of who calls `addLiquidity`. [4](#0-3) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of counterparties is fully bypassed for any user who routes through `MetricOmmSimpleRouter`. Unauthorized users can execute swaps against restricted liquidity, draining LP assets at oracle-quoted prices that the pool admin intended to reserve for specific parties. This is a direct loss of LP principal and a broken core pool invariant (access-controlled swap).

---

### Likelihood Explanation

The trigger requires the router to be allowlisted for the pool — a natural and expected admin action, since allowlisted users need the router to perform multi-hop or slippage-protected swaps. Any pool that (a) uses `SwapAllowlistExtension` and (b) allowlists the router is immediately exploitable by any address. No privileged role, special token, or malicious setup is required from the attacker's side.

---

### Recommendation

The extension must gate the **original user**, not the intermediary. Two complementary fixes:

1. **Pass `tx.origin` or a user-supplied identity through `extensionData`** — but `tx.origin` is unsafe in a general context.

2. **Preferred**: Mirror the `DepositAllowlistExtension` design: have the router pass the original `msg.sender` as the `recipient` or encode it in `extensionData`, and have the extension read from there. Alternatively, require that the pool's `sender` argument always be the economic actor (i.e., the router must forward the original caller as `sender` rather than acting as the caller itself).

A minimal diff for the extension:

```diff
- function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
+ function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata extensionData)
      external view override returns (bytes4)
  {
+     // If extensionData encodes an originator, use it; otherwise fall back to sender.
+     address swapper = extensionData.length >= 20 ? abi.decode(extensionData, (address)) : sender;
-     if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
+     if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][swapper]) {
          revert IMetricOmmPoolActions.NotAllowedToSwap();
      }
```

And in `MetricOmmSimpleRouter`, encode `msg.sender` into `extensionData` before forwarding to the pool.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension (beforeSwap order set)
  allowedSwapper[pool][alice]  = true   // alice is the intended user
  allowedSwapper[pool][router] = true   // admin allowlists router so alice can use it
  allowedSwapper[pool][bob]    = false  // bob is NOT allowlisted

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient=bob, ...)   [msg.sender = router]
    → pool calls extension.beforeSwap(sender=router, ...)
    → extension checks allowedSwapper[pool][router] == true  ✓
    → swap executes — bob receives tokens from restricted LP
``` [5](#0-4) [6](#0-5) [1](#0-0)

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
