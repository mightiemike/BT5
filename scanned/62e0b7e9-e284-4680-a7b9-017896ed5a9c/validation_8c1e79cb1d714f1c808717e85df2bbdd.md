### Title
SwapAllowlistExtension Gates Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument it receives, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the user. If the router address is allowlisted for a pool, every user — including those explicitly not allowlisted — can bypass the curated-pool restriction by calling any router entry point.

---

### Finding Description

**Call chain when a user swaps through the router:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient=user, ...)          // msg.sender = router
              → _beforeSwap(sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → checks allowedSwapper[pool][router]
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
  msg.sender,   // ← router address when called via router
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged:

```solidity
// ExtensionCalling.sol:160-176
abi.encodeCall(IMetricOmmExtensions.beforeSwap,
  (sender, recipient, ...))   // sender = router
```

`SwapAllowlistExtension.beforeSwap` then checks the router's allowlist status, not the actual user's:

```solidity
// SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` = pool, `sender` = router. The check is `allowedSwapper[pool][router]`.

**Contrast with `DepositAllowlistExtension`**, which correctly gates the economic actor:

```solidity
// DepositAllowlistExtension.sol:38
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
```

The deposit extension checks `owner` (the LP position holder), not `sender` (the immediate caller). The swap extension has no equivalent — it checks only the immediate caller, which is the router.

**Trigger condition**: A pool admin allowlists the router address — a plausible configuration when the admin wants to allow "standard" periphery usage while still restricting direct pool access to specific addresses. Once `allowedSwapper[pool][router] = true`, the allowlist is completely open to any user who calls through the router.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC'd market makers, whitelisted institutions) loses all swap-side access control the moment the router is allowlisted. Any unpermissioned user can call `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutput` / `exactOutputSingle` and execute swaps against the pool. LP principal is exposed to unauthorized counterparties, defeating the entire purpose of the curated pool design. This is a direct broken-core-functionality impact on LP funds.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical periphery swap entry point. A pool admin who wants to allow "normal" router-based trading while restricting direct pool access will naturally allowlist the router. The admin has no on-chain signal that doing so opens the pool to all users — the allowlist UI/admin surface only exposes `setAllowedToSwap(pool, address, bool)` with no warning about the router identity substitution. The bypass requires no special privileges, no flash loans, and no multi-step setup beyond a single router call.

---

### Recommendation

The extension must gate the **economic actor**, not the immediate caller. Two options:

1. **Pass the original user through `extensionData`**: Require the router to encode the original `msg.sender` in `extensionData` and have the extension decode and check that address. This requires a coordinated change to the router and extension.

2. **Align with the deposit pattern**: Expose a `recipient` parameter check instead of (or in addition to) `sender`, since `recipient` is the address that receives the output tokens and is the closest proxy for the economic actor in a swap. The pool already passes `recipient` as the second argument to `beforeSwap`.

---

### Proof of Concept

```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Deploy pool with ext as beforeSwap hook
// Admin allowlists the router (intending to allow "standard" usage)
ext.setAllowedToSwap(pool, address(router), true);

// Attacker (not individually allowlisted)
address attacker = makeAddr("attacker");
// attacker is NOT in allowedSwapper[pool][attacker]

// Attacker calls router — router becomes msg.sender at the pool
router.exactInputSingle(ExactInputSingleParams({
  pool: pool,
  recipient: attacker,
  zeroForOne: true,
  amountIn: 1000,
  amountOutMinimum: 0,
  priceLimitX64: 0,
  deadline: block.timestamp,
  tokenIn: token0,
  extensionData: ""
}));
// ↑ succeeds: extension checks allowedSwapper[pool][router] == true
// attacker has swapped against the curated pool without being individually allowlisted
```

The check `allowedSwapper[pool][router]` passes, the swap executes, and the attacker receives output tokens from a pool that was supposed to be restricted to specific counterparties. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
