The trace is complete. Here is the full analysis:

---

### Title
`SwapAllowlistExtension` gates the router address, not the end user — any caller bypasses per-user allowlist via router - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the end user. If the pool admin allowlists the router address (a natural configuration to enable router-based swaps), every user — including non-allowlisted ones — bypasses the individual swap gate.

### Finding Description

**Call chain:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle()`. [1](#0-0) 

2. The router calls `pool.swap(...)` — so `msg.sender` inside the pool is the **router address**. [2](#0-1) 

3. The pool dispatches `_beforeSwap(msg.sender, ...)`, forwarding the router address as `sender` to the extension. [3](#0-2) 

4. `SwapAllowlistExtension.beforeSwap` evaluates:
   ```solidity
   if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
       revert IMetricOmmPoolActions.NotAllowedToSwap();
   }
   ```
   Here `msg.sender` = pool, `sender` = router. The check resolves to `allowedSwapper[pool][router]`. [4](#0-3) 

**The flaw:** The extension is documented as "Gates `swap` by swapper address, per pool" and exposes `setAllowedToSwap(pool, swapper, allowed)` for per-user control. But the `sender` it receives is always the immediate caller of `pool.swap()` — the router — not the originating user. A pool admin who calls `setAllowedToSwap(pool, router, true)` (a natural step to enable router-based swaps) inadvertently opens the gate to **all** users, because every router call presents the same `sender = router`. [5](#0-4) 

There is no mechanism in the extension to recover the originating EOA from the router call. The extension cannot simultaneously allow router-based swaps and enforce per-user restrictions.

### Impact Explanation

Any non-allowlisted user can swap in a pool that is intended to be restricted to specific addresses, simply by routing through `MetricOmmSimpleRouter`. The core access-control invariant of `SwapAllowlistExtension` — that only individually allowlisted addresses may swap — is broken for all router-mediated paths. This constitutes broken core pool functionality (the allowlist guard is rendered ineffective).

### Likelihood Explanation

- Pool admins who want to support router-based swaps will naturally allowlist the router address.
- Once the router is allowlisted, the bypass requires zero privilege: any user calls `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` on the public router.
- The existing test suite confirms the extension is tested only with direct callers (`callers[0]`), not with the router as the allowlisted entity, so the gap is not caught by existing coverage. [6](#0-5) 

### Recommendation

The extension must receive the originating user identity, not the immediate `pool.swap()` caller. Two options:

1. **Pass `msg.sender` from the router as `extensionData`** and have `SwapAllowlistExtension` decode it — but this is forgeable by any direct caller.
2. **Preferred:** Add an `originSender` field to the swap hook arguments (alongside `sender`) that the pool populates from a trusted periphery context, or require that the router passes the originating user through `extensionData` with a signature/hash that the extension can verify.

Alternatively, document clearly that `SwapAllowlistExtension` only gates direct `pool.swap()` callers and cannot enforce per-user restrictions for router-mediated swaps, and provide a separate mechanism for router-aware allowlisting.

### Proof of Concept

```solidity
// Pool admin sets up allowlist: only router is allowlisted (to enable router swaps)
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Non-allowlisted user (attacker) calls through the router
vm.prank(attacker); // attacker is NOT in allowedSwapper[pool]
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    tokenIn: address(token0),
    tokenOut: address(token1),
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    recipient: attacker,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// Swap succeeds — allowlist bypassed because sender == router, which is allowlisted
```

The pool's `beforeSwap` hook receives `sender = address(router)`, which passes `allowedSwapper[pool][router] == true`, and the swap executes despite `attacker` never being individually allowlisted. [7](#0-6) [8](#0-7)

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L66-86)
```text
  /// @inheritdoc IMetricOmmSimpleRouter
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-13)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
