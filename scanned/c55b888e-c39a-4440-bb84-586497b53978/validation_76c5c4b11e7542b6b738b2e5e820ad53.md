The vulnerability is real and confirmed. Here is the complete trace:

**Call chain:**
1. User calls `MetricOmmSimpleRouter.exactInputSingle(...)` [1](#0-0) 
2. Router calls `IMetricOmmPoolActions(params.pool).swap(...)` — making the router `msg.sender` to the pool [2](#0-1) 
3. Pool passes `msg.sender` (= router) as `sender` to `_beforeSwap` [3](#0-2) 
4. `_beforeSwap` encodes `sender` (= router) and dispatches to the extension [4](#0-3) 
5. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` = pool and `sender` = **router address** — never the actual user [5](#0-4) 

---

### Title
Swap Allowlist Bypass via Router: `sender` Is Router Address, Not End User — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` — the immediate caller of `pool.swap`. When the official `MetricOmmSimpleRouter` intermediates, `sender` is always the router address. If the router is allowlisted (a natural admin action to enable router-mediated swaps), every user — including those not on the allowlist — can bypass the per-user gate.

### Finding Description
`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // <-- router address when called via router
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The actual end user's address is never inspected. A pool admin who allowlists the router address (e.g., `setAllowedToSwap(pool, address(router), true)`) to permit router-mediated swaps for their curated users inadvertently opens the gate to all users.

### Impact Explanation
Any user who is not on the per-user allowlist can execute swaps on a curated pool by routing through `MetricOmmSimpleRouter`. The allowlist — the sole access-control mechanism for swap-gated pools — is completely bypassed. This breaks core pool functionality and, depending on the pool's purpose (e.g., institutional-only, KYC-gated), constitutes a direct policy violation that can lead to unauthorized fund flows.

### Likelihood Explanation
High. `MetricOmmSimpleRouter` is the canonical public periphery swap contract. Pool admins who want to support router-mediated swaps for their allowlisted users will naturally allowlist the router address. The bypass requires no special privileges — any EOA or contract can call the router.

### Recommendation
Pass the original user identity through the call chain. Two options:

1. **Preferred**: Have the pool accept an explicit `swapper` parameter (separate from `msg.sender`) that the router populates with `msg.sender` before calling the pool, and pass that through to extensions as `sender`.
2. **Alternative**: In `SwapAllowlistExtension`, do not allowlist router addresses at all; instead, document that allowlisted users must call the pool directly. This is a UX regression but closes the bypass.

### Proof of Concept
```solidity
// Setup: pool with SwapAllowlistExtension
// Admin allowlists router (intending to allow router-mediated swaps for curated users)
swapExtension.setAllowedToSwap(address(pool), address(router), true);
// Admin does NOT allowlist attacker
// swapExtension.setAllowedToSwap(address(pool), attacker, false); // default

// Attack: attacker calls router, not pool directly
vm.prank(attacker); // attacker is NOT on the allowlist
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    ...
}));
// sender seen by SwapAllowlistExtension = address(router) → allowlisted → swap succeeds
// Invariant violated: unauthorized user executed a swap on a curated pool
```

### Citations

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```
