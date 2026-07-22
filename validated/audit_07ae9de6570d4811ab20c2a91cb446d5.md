The call chain is fully traceable. Here is the complete analysis:

---

**Call chain trace:**

1. `MetricOmmPool::swap()` calls `_beforeSwap(msg.sender, ...)` — `msg.sender` here is whoever called `pool.swap()`. [1](#0-0) 

2. `ExtensionCalling::_beforeSwap` encodes `sender` (= the pool's `msg.sender`) and forwards it to each configured extension via `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))`. [2](#0-1) 

3. `SwapAllowlistExtension::beforeSwap` receives that `sender` and checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. [3](#0-2) 

4. `MetricOmmSimpleRouter::exactInputSingle` calls `pool.swap()` directly — the router is `msg.sender` of that call, so `sender` delivered to the hook is the **router address**, not the original end-user. [4](#0-3) 

The original end-user's address (`msg.sender` of the router call) is stored only in transient callback context for payment, never forwarded to the pool or the hook. [5](#0-4) 

---

**The concrete identity mismatch:**

| Flow | `sender` seen by hook | Check performed |
|---|---|---|
| Direct `pool.swap()` | end-user address | `allowedSwapper[pool][user]` |
| Via `MetricOmmSimpleRouter` | router address | `allowedSwapper[pool][router]` |

If the pool admin allowlists the router (the natural step to let allowlisted users access the router), the check becomes `allowedSwapper[pool][router] == true` for **every caller of the router**, regardless of who they are. Any unprivileged address can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` and the hook passes.

The "stop-loss threshold / watermark boundary" framing in the question is noise — `SwapAllowlistExtension` contains no boundary math, no drawdown logic, and no watermark state. The actual flaw is purely the identity mismatch described above.

---

**Impact assessment:**

The allowlist extension is documented as gating `swap` by swapper address per pool. [6](#0-5) 

When the router is allowlisted, that gate is fully bypassed for all router callers. This breaks the core functionality the extension was designed to provide. However, the direct financial impact (fund loss, pool insolvency, bad-price execution) is not automatic — it depends on what the pool admin was trying to protect against with the allowlist. The allowlist itself does not enforce price bounds or stop-loss logic; it only restricts who can initiate a swap. An unauthorized swap still executes at the pool's oracle-derived price, so there is no inherent bad-price execution from the bypass alone.

---

### Title
Router-mediated swaps pass the router address as `sender` to `SwapAllowlistExtension::beforeSwap`, allowing any user to bypass per-user allowlist restrictions — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension::beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the immediate caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router address, not the end-user. Allowlisting the router to enable router access for permitted users simultaneously grants access to all users.

### Finding Description
`MetricOmmPool::swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it unchanged to every configured extension. `SwapAllowlistExtension::beforeSwap` uses this value to look up `allowedSwapper[msg.sender][sender]` (pool → swapper). When the router calls `pool.swap()`, `sender = router`. If the pool admin adds the router to the allowlist (the only way to let allowlisted users use the router), the check `allowedSwapper[pool][router]` returns `true` for every caller of the router, regardless of their identity. The original end-user address is stored only in the router's transient callback context and is never surfaced to the pool or the hook.

### Impact Explanation
Any pool that (a) uses `SwapAllowlistExtension` to restrict swappers and (b) allowlists the router so that permitted users can access it is effectively open to all users. The allowlist provides no protection in the router-mediated path. Depending on the pool's purpose (e.g., institutional-only, KYC-gated, or rate-limited pools), this can allow unauthorized actors to drain liquidity or execute swaps the pool designers intended to block.

### Likelihood Explanation
The router is the standard user-facing entry point. Any pool admin who wants to restrict swappers but still allow router access will naturally allowlist the router, triggering the bypass. The path requires no special privileges, no malicious setup, and no non-standard tokens.

### Recommendation
Pass the original end-user's identity through the call chain. One approach: have the router encode the original `msg.sender` in `extensionData` and have the extension read it from there (with appropriate authentication). A cleaner approach is for the pool to accept an explicit `originator` parameter distinct from `msg.sender`, or for the router to use a dedicated forwarding mechanism that the extension can verify (e.g., checking transient storage set by the router before calling the pool).

### Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is permitted.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is added so Alice can use it.
4. Attacker (Bob, not allowlisted) calls `MetricOmmSimpleRouter::exactInputSingle(...)`.
5. Router calls `pool.swap(...)` with `msg.sender = router`.
6. Hook checks `allowedSwapper[pool][router] == true` → passes.
7. Bob's swap executes on the restricted pool. Assert: `allowedSwapper[pool][bob] == false` yet the swap succeeded. [7](#0-6) [8](#0-7)

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
