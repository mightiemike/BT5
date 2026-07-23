### Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the actual user, allowing any unprivileged caller to bypass a per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is documented as gating swaps "by swapper address, per pool." However, `beforeSwap` receives `sender` which is the `msg.sender` of the pool's `swap()` call — the router contract — not the originating user. When a pool admin allowlists the router to enable router-mediated swaps for their curated users, every unprivileged address can bypass the per-user allowlist by routing through `MetricOmmSimpleRouter`.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct) and `sender` is the `msg.sender` of the pool's `swap()` call. When a user goes through `MetricOmmSimpleRouter.exactInputSingle`, the call chain is:

```
user → router.exactInputSingle() → pool.swap(recipient, ...) → extension.beforeSwap(msg.sender=router, ...)
``` [2](#0-1) 

The pool passes its own `msg.sender` (the router) as `sender` to the extension:

```solidity
_beforeSwap(
    msg.sender,   // ← router address, not the originating user
    recipient,
    ...
);
``` [3](#0-2) 

So the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

This is structurally inconsistent with `DepositAllowlistExtension`, which correctly ignores `sender` (the liquidity adder) and gates on `owner` (the economic actor):

```solidity
function beforeAddLiquidity(address, address owner, ...)
    ...
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
``` [4](#0-3) 

The `beforeSwap` interface has no equivalent "economic actor" parameter beyond `sender` and `recipient`, so the extension has no way to recover the originating user without the router forwarding it explicitly.

The same bypass applies to all router paths: `exactInput` (multi-hop), `exactOutputSingle`, and `exactOutput` (recursive callback), because in every case the router is the `msg.sender` of each `pool.swap()` call. [5](#0-4) 

---

### Impact Explanation

A pool admin who configures a per-user swap allowlist (e.g., for KYC/compliance) must also allowlist the router if they want their curated users to be able to use the standard periphery. The moment the router is allowlisted, `allowedSwapper[pool][router] == true`, and the guard passes for **every** caller regardless of whether they are individually allowlisted. The allowlist is completely defeated for all router-mediated swaps. Non-allowlisted users can trade freely on a pool that was intended to be curated, causing direct policy bypass and potential regulatory or financial harm to the pool's LP base.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported swap entrypoint in the periphery. Any pool that deploys `SwapAllowlistExtension` and wants its curated users to use the router faces a forced choice: either allowlist the router (opening the bypass) or leave the router un-allowlisted (breaking the router for everyone). The bypass is reachable by any unprivileged address with no special preconditions beyond the pool admin having made the natural configuration choice of allowlisting the router.

---

### Recommendation

Two complementary fixes:

1. **Router-level**: Have `MetricOmmSimpleRouter` forward the originating user's address through `extensionData` (e.g., ABI-encoded as a prefix). The extension can then decode and check the real caller.

2. **Extension-level**: Add an `originSender` field to the `beforeSwap` interface, or have the extension accept a trusted forwarder list and decode the real caller from `extensionData` when `sender` is a known forwarder.

As a minimum, the `SwapAllowlistExtension` NatSpec and pool configuration documentation must warn that allowlisting the router grants unrestricted swap access to all users, and that per-user gating requires direct pool calls only.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowAllSwappers[pool] = false
  - allowedSwapper[pool][alice] = true       // alice is KYC'd
  - allowedSwapper[pool][router] = true      // admin adds router so alice can use it
  - bob is NOT allowlisted

Attack:
  1. bob calls router.exactInputSingle({pool: pool, ...})
  2. router calls pool.swap(recipient=bob, ...)
  3. pool calls extension.beforeSwap(sender=router, ...)
  4. extension checks: allowedSwapper[pool][router] == true → passes
  5. bob's swap executes on the curated pool

Direct call check (for comparison):
  1. bob calls pool.swap(...) directly
  2. pool calls extension.beforeSwap(sender=bob, ...)
  3. extension checks: allowedSwapper[pool][bob] == false → reverts NotAllowedToSwap ✓

Result: bob bypasses the allowlist via the router.
``` [1](#0-0) [6](#0-5)

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
