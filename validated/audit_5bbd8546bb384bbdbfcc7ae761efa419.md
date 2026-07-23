The call chain is fully traceable. Let me confirm the exact `sender` binding at each hop.

The call chain is fully confirmed. Here is the exact trace:

**Step 1:** EOA → `MetricOmmSimpleRouter.exactInputSingle()` (`msg.sender` = EOA) [1](#0-0) 

**Step 2:** Router → `pool.swap(params.recipient, ...)` — the router is now `msg.sender` inside the pool. No original EOA identity is forwarded. [2](#0-1) 

**Step 3:** Pool calls `_beforeSwap(msg.sender, ...)` — `msg.sender` here is the **router address**, so `sender` = router. [3](#0-2) 

**Step 4:** `SwapAllowlistExtension.beforeSwap` receives `sender` = router, `msg.sender` = pool. The guard evaluates `allowedSwapper[pool][router]`. [4](#0-3) 

If the pool admin has called `setAllowedToSwap(pool, router, true)`, this check passes for **every EOA** that routes through the router — the original caller's identity is never consulted. [5](#0-4) 

---

### Title
`SwapAllowlistExtension` checks router address instead of originating EOA, allowing any user to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` gates swaps on the `sender` argument, which the pool binds to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the originating EOA. Any pool admin who allowlists the router address (a natural action to enable router-mediated swaps for their users) inadvertently opens the pool to all users.

### Finding Description
`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`:

```solidity
_beforeSwap(msg.sender, recipient, ...);   // msg.sender = router
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) revert ...
// resolves to: allowedSwapper[pool][router]
```

`MetricOmmSimpleRouter.exactInputSingle` stores the originating EOA only in transient callback context (`_setNextCallbackContext(..., msg.sender, ...)`) for payment purposes; it is never forwarded to the pool as the swap `sender`. The pool's `swap` signature has no `sender` parameter — the pool always uses `msg.sender`. [6](#0-5) 

### Impact Explanation
The swap allowlist's core invariant — "only explicitly allowlisted addresses may swap" — is broken. Any unprivileged EOA can bypass it by calling `exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) on the public router. This constitutes broken core pool functionality: the access-control extension produces no effective gate for router-mediated swaps.

### Likelihood Explanation
A pool admin who wants to allow their allowlisted users to trade via the router has no other option than to allowlist the router address itself. The extension provides no mechanism to propagate the originating EOA through an intermediary. The misconfiguration is therefore a predictable consequence of normal, non-malicious admin usage.

### Recommendation
The extension (or the pool) must propagate the originating caller identity through the router. Two options:

1. **Pass originator in `extensionData`:** The router encodes `msg.sender` into `extensionData`; `SwapAllowlistExtension` decodes and checks it. This requires a convention between router and extension.
2. **Add an explicit `originator` field to the swap interface:** The pool's `swap` signature accepts an `originator` address (defaulting to `msg.sender` for direct calls); the router passes `msg.sender` there. The extension checks `originator` instead of `sender`.

### Proof of Concept
```solidity
// 1. Deploy pool with SwapAllowlistExtension in BEFORE_SWAP_ORDER
// 2. Admin allowlists only the router: setAllowedToSwap(pool, address(router), true)
// 3. Non-allowlisted EOA calls:
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    tokenIn: token0,
    ...
}));
// 4. pool.swap() is called with msg.sender = router
// 5. _beforeSwap(router, ...) → allowedSwapper[pool][router] == true → passes
// 6. Swap executes for the non-allowlisted EOA — allowlist bypassed
``` [7](#0-6) [3](#0-2) [8](#0-7)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L188-195)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external returns (int128 amount0Delta, int128 amount1Delta);
```
