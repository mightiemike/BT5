### Title
SwapAllowlistExtension Gates Router Address Instead of End-User, Enabling Complete Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, the router becomes `msg.sender` of `pool.swap()`, so the extension checks the router's address rather than the end-user's address. If the pool admin allowlists the router to support router-mediated swaps, every user — including explicitly disallowed ones — can bypass the per-user swap allowlist by routing through the public router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check as:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the only caller that passes `onlyPool`), and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`, which is always `msg.sender` of `pool.swap()`:

```solidity
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
)
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` passes this value unchanged as the `sender` argument to every configured extension:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
``` [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` with itself as `msg.sender`:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [4](#0-3) 

So the extension receives `sender = address(router)`, not the end-user's address. The allowlist lookup becomes `allowedSwapper[pool][router]`.

**The bypass path:** A pool admin who wants to support router-mediated swaps for their allowlisted users must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, `allowedSwapper[pool][router] == true` for every call that arrives through the router — regardless of which end-user initiated it. Any disallowed user can then call `router.exactInputSingle()` and the extension passes unconditionally.

The `DepositAllowlistExtension` does not share this flaw: it checks the `owner` argument (second parameter), which is the position owner supplied by the caller, not the adder contract's address. [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC-gated users, institutional market makers, or whitelisted bots) is completely open to any user once the router is allowlisted. Disallowed users can execute swaps at oracle-derived prices, causing adverse selection against LP positions. Because the pool uses external oracle pricing with no internal price discovery, LP funds are directly at risk from trades that the allowlist was intended to prevent. This is a direct loss of LP principal above Sherlock thresholds.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical supported periphery swap path. A pool admin who deploys a curated pool and wants allowlisted users to be able to use the standard router must allowlist the router address. The admin's mental model — "I am allowlisting the router as a trusted intermediary for my approved users" — does not match the actual effect — "I am allowlisting every user who routes through the router." This is a predictable operational mistake with a clear, unprivileged trigger (any user calling the public router).

---

### Recommendation

The `SwapAllowlistExtension` must gate on the end-user's identity, not the direct caller of `pool.swap()`. Two viable approaches:

1. **Pass the original user through `extensionData`:** The router encodes `msg.sender` into `extensionData` for each hop, and the extension decodes and checks it. This requires a convention between the router and the extension.

2. **Check `sender` only when it is not a known router; otherwise decode the real user from `extensionData`:** The extension can maintain a registry of trusted routers and require that calls from those routers include the real user in `extensionData`.

3. **Require direct pool calls for allowlisted pools:** Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this by not allowlisting the router, accepting that allowlisted users must call the pool directly.

---

### Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension as beforeSwap hook
  admin calls setAllowedToSwap(pool, userA, true)       // allowlist userA
  admin calls setAllowedToSwap(pool, router, true)       // allowlist router to support router swaps
  LP adds liquidity to pool

Attack (userB is NOT allowlisted):
  userB calls router.exactInputSingle({pool: pool, tokenIn: ..., ...})
    → router calls pool.swap(recipient, zeroForOne, amount, ..., extensionData)
      msg.sender of pool.swap() = address(router)
    → pool calls _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
      checks: allowedSwapper[pool][router] == true  ← PASSES
    → swap executes at oracle price
    → userB receives output tokens

Result: userB, who is not on the allowlist, successfully swaps on the restricted pool.
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
