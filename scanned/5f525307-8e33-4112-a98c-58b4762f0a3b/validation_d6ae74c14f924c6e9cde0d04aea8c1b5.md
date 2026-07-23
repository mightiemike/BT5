### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Allowing Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which is `msg.sender` of the pool call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router's address, not the actual user. A pool admin who allowlists the router (the natural action to let their curated users access the router) inadvertently opens the pool to every user, completely defeating the allowlist.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

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
``` [1](#0-0) 

The pool populates `sender` with `msg.sender` of the pool call:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← sender: whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
    );
``` [3](#0-2) 

The pool's `msg.sender` is the **router**, so `sender` passed to the extension is the router address. The actual user who called the router is stored only in transient storage as the payer for the callback — it is never forwarded to the extension. The `recipient` parameter (the address that actually receives the output tokens) is also ignored by the extension (unnamed second argument).

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

---

### Impact Explanation

**Critical/High — Allowlist curation failure with direct fund-flow consequence.**

Two concrete outcomes:

1. **Bypass (primary impact):** A pool admin who wants their allowlisted users to be able to use the router must allowlist the router address. Once `allowedSwapper[pool][router] = true`, every user on the network can call `exactInputSingle` / `exactInput` through the router and swap in the curated pool. The allowlist is completely nullified for all router-routed swaps.

2. **Broken functionality (secondary impact):** If the admin does *not* allowlist the router, individually allowlisted users cannot use the router at all — they must call the pool directly. This breaks the expected periphery integration for every curated pool.

In either case the invariant stated in the extension's own NatDoc — *"Gates `swap` by swapper address, per pool"* — is violated for all router-mediated swaps. [4](#0-3) 

---

### Likelihood Explanation

**High.** `MetricOmmSimpleRouter` is the primary supported periphery entry point. Any pool that deploys `SwapAllowlistExtension` and wants its users to access the router will trigger this path. The admin action of allowlisting the router is the natural, expected configuration step — it is not a malicious or unusual action. No special privileges beyond being a normal user are required to exploit the bypass once the router is allowlisted.

---

### Recommendation

The extension must check the **actual initiating user**, not the intermediary caller. Two options:

1. **Pass the real user through `extensionData`:** Have the router encode `msg.sender` into `extensionData` and have the extension decode and verify it. This requires a convention between router and extension.

2. **Check `recipient` instead of `sender`:** If the semantic intent is to gate who receives tokens from the pool, the extension should check `recipient` (the address that actually receives output tokens). This is the direct analog of the fix recommended in the external report — bind the guard to the actor who economically benefits.

3. **Enforce `sender == recipient` in the extension:** Require that the swap initiator and the token receiver are the same address, preventing third-party routing on behalf of non-allowlisted users.

---

### Proof of Concept

```
Setup:
  - Pool deployed with SwapAllowlistExtension
  - Admin allowlists alice: allowedSwapper[pool][alice] = true
  - Admin allowlists router so alice can use it: allowedSwapper[pool][router] = true

Attack (bob is NOT allowlisted):
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({
         pool: pool,
         recipient: bob,   // bob receives the output tokens
         ...
     })
  2. Router calls pool.swap(bob, ...) — msg.sender of pool call = router
  3. Pool calls extension.beforeSwap(router, bob, ...)
  4. Extension checks allowedSwapper[pool][router] → true ✓
  5. Swap executes; bob receives tokens from the curated pool
  6. The allowlist never checked bob's address at any point
``` [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-11)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
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
