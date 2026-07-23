### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Enabling Full Allowlist Bypass on Curated Pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the pool's `msg.sender` — the `MetricOmmSimpleRouter` contract address — not the actual end user. Any user who routes through the router on a pool that has allowlisted the router bypasses the per-user swap gate entirely, trading on a curated pool without individual authorization.

---

### Finding Description

The call chain for a router-mediated swap is:

```
User → MetricOmmSimpleRouter.exactInputSingle()
     → pool.swap(recipient, ...) [msg.sender = router]
     → _beforeSwap(msg.sender=router, recipient, ...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool — the router: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap`, the router is `msg.sender` of that call: [4](#0-3) 

The actual end user (`msg.sender` of `exactInputSingle`) is stored only in transient callback context and is never surfaced to the extension. The extension has no way to see the real user; it only sees the router address.

---

### Impact Explanation

**Scenario A — Router is allowlisted (allowlist bypass):** A pool admin who wants to permit router-mediated swaps must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, `allowedSwapper[pool][router]` is `true` for every call that arrives through the router, regardless of who the actual end user is. Any address — including addresses the admin explicitly never allowlisted — can call `exactInputSingle` on the router and trade on the curated pool. The per-user curation is completely defeated.

**Scenario B — Router is not allowlisted (legitimate users blocked):** If the admin does not allowlist the router, every router-mediated swap reverts with `NotAllowedToSwap`, even for users who are individually allowlisted. Allowlisted users must call `pool.swap` directly, breaking the standard periphery UX.

Both outcomes are fund-impacting: Scenario A allows unauthorized principals to drain liquidity from a curated pool at oracle prices; Scenario B makes the pool's swap flow unusable for the intended user set.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported swap entrypoint documented in the protocol. Pool admins who deploy a `SwapAllowlistExtension` to restrict trading to a curated set of addresses will naturally also allowlist the router to enable normal UX — triggering Scenario A. The trigger requires only a standard `exactInputSingle` call from any address; no privileged access, no special setup, and no non-standard token behavior is needed.

---

### Recommendation

Pass the economically relevant actor — the end user — through the extension data or as a dedicated field, and gate on that. Two concrete options:

1. **Router-side**: Have `MetricOmmSimpleRouter` encode `msg.sender` into `extensionData` before calling `pool.swap`, and update `SwapAllowlistExtension.beforeSwap` to decode and check that address when present.

2. **Extension-side**: Change `beforeSwap` to check `recipient` instead of `sender` when the pool is configured for router-only access, or require the pool admin to allowlist individual users directly and never allowlist the router address.

The deposit-side extension already handles this correctly by checking `owner` (the position owner passed explicitly by the caller) rather than `sender` (the adder contract): [5](#0-4) 

The swap allowlist should adopt the same pattern: gate on the identity that the pool designers intend to restrict, not on the intermediary contract that happens to be `msg.sender` of the pool call.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps for allowlisted users.
3. `blockedUser` (never individually allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
4. The pool calls `_beforeSwap(sender=router, ...)`.
5. `SwapAllowlistExtension` evaluates `allowedSwapper[pool][router] == true` → passes.
6. `blockedUser` successfully swaps on the curated pool, bypassing the allowlist entirely.

The invariant broken: `allowedSwapper[pool][actualUser]` is never consulted; the check resolves to `allowedSwapper[pool][router]` for all router-mediated swaps. [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
```text
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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-176)
```text
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
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-41)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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
