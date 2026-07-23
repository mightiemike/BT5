Let me look at the `MetricOmmPoolLiquidityAdder` and the full `ExtensionCalling.sol` to understand the complete hook flow and identity binding.

### Title
SwapAllowlistExtension gates the router address instead of the end user, allowing any user to bypass the per-user swap allowlist via MetricOmmSimpleRouter - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool receives `msg.sender = router`, so the extension checks whether the **router** is allowlisted — not the actual end user. If the router is allowlisted, every user on the network can bypass the per-user swap restriction.

---

### Finding Description

The call chain is:

```
User → MetricOmmSimpleRouter.exactInputSingle(pool, ...) 
     → pool.swap(recipient=user, ...) [msg.sender = router]
     → _beforeSwap(msg.sender=router, recipient=user, ...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
     → allowedSwapper[pool][router] → passes
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← this is the router, not the end user
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this unchanged to the extension:

```solidity
// ExtensionCalling.sol L160-176
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)  // sender = router
)
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The check passes if `allowedSwapper[pool][router]` is `true`. A pool admin who allowlists the router to enable router-based swaps inadvertently opens the gate for every user on the network.

This is structurally different from `DepositAllowlistExtension`, which correctly checks `owner` (the address that receives LP shares) — the economically relevant actor for deposits. For swaps, the economically relevant actor is the end user, not the router intermediary.

The `IMetricOmmPoolActions` NatSpec at line 147 explicitly documents the operator pattern for deposits: *"msg.sender pays but need not equal owner"* — confirming `owner` is the intended gated identity for deposits. No equivalent clarification exists for swaps, and the extension's own title says it "Gates `swap` by swapper address, per pool," implying the end user is the intended gated identity.

---

### Impact Explanation

A pool admin configures `SwapAllowlistExtension` to restrict swaps to specific addresses (e.g., KYC-verified market makers, whitelisted counterparties, or protocol-internal addresses). The admin also allowlists the `MetricOmmSimpleRouter` so that permitted users can trade through the standard periphery. Any non-allowlisted user can then call `exactInputSingle` or `exactInput` on the router, which forwards the swap to the pool with `msg.sender = router`. The extension sees the allowlisted router and passes. The unauthorized user executes a full swap — receiving output tokens and paying input tokens — in a pool that was supposed to be restricted to them. The allowlist provides zero protection against router-mediated access.

---

### Likelihood Explanation

The scenario requires the pool admin to allowlist the router. This is a natural and expected action: any pool that wants to support the standard periphery must allowlist the router. A pool admin who configures both a `SwapAllowlistExtension` (to restrict users) and allowlists the router (to support normal UX) will unknowingly negate the restriction entirely. The two actions are independently reasonable and their interaction is not documented or guarded.

---

### Recommendation

The extension must gate the economically relevant actor. Two options:

1. **Check `recipient` instead of `sender`**: For swaps, the `recipient` is the address that receives output tokens — the economically relevant actor. Change line 37 to check `recipient` (the second argument) instead of `sender`.

2. **Require direct pool calls only**: Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this by checking `sender == recipient` or by requiring `sender` to not be a known router.

The cleanest fix consistent with the deposit extension's design is to check `recipient` (the address that benefits from the swap output), mirroring how `DepositAllowlistExtension` checks `owner` (the address that receives LP shares).

---

### Proof of Concept

**Setup:**
- Pool is deployed with `SwapAllowlistExtension` as a `beforeSwap` hook
- Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-based swaps
- Pool admin does NOT allowlist `attacker`
- `attacker` is not in `allowedSwapper[pool]`

**Attack:**
```solidity
// attacker calls the public router — no special permissions needed
router.exactInputSingle(
    ExactInputSingleParams({
        pool: restrictedPool,
        tokenIn: token0,
        recipient: attacker,
        amountIn: 1_000e18,
        amountOutMinimum: 0,
        zeroForOne: true,
        priceLimitX64: 0,
        deadline: block.timestamp,
        extensionData: ""
    })
);
// SwapAllowlistExtension checks allowedSwapper[pool][router] → true → passes
// attacker receives token1 output from a pool they were supposed to be barred from
```

**Result:** `attacker` successfully swaps in a pool whose allowlist was supposed to exclude them. The `allowedSwapper[pool][attacker]` mapping is never consulted. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L111-113)
```text
  /// @notice Swap allowlist rejected `msg.sender`.
  /// @dev Only `swap` checks this when `SWAP_ALLOWLIST_PROVIDER` is set; `simulateSwapAndRevert` does not, so a passing simulation does not imply an allowed live swap.
  error NotAllowedToSwap();
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
