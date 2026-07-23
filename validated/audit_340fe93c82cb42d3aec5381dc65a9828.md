### Title
`SwapAllowlistExtension` gates the router address instead of the actual swapper, allowing any user to bypass the per-user allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which is `msg.sender` of the pool's `swap` call. When a user swaps through `MetricOmmSimpleRouter`, `sender` equals the router address, not the actual user. If the pool admin allowlists the router — a natural step to enable router-based swaps — every non-allowlisted user can bypass the per-user gate by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

```solidity
_beforeSwap(
    msg.sender,   // ← router address when called via MetricOmmSimpleRouter
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

`SwapAllowlistExtension.beforeSwap` then checks: [2](#0-1) 

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool; `sender` is whoever called `pool.swap()`. When the path is `user → MetricOmmSimpleRouter → pool.swap()`, `sender` = router. [3](#0-2) 

The router calls `pool.swap(params.recipient, ...)` directly and does **not** forward the actual caller's address to the pool or to the extension. The actual user's address is stored only in transient storage for the payment callback — the extension never sees it. [4](#0-3) 

The allowlist is keyed `allowedSwapper[pool][swapper]`. When the pool admin wants to allow router-based swaps for their allowlisted users, the natural action is to add the router: `allowedSwapper[pool][router] = true`. This single entry opens the gate for **every** user who calls through the router, regardless of whether they are individually allowlisted.

This is the direct analog to the `totalExcludedSupply` drift: the guard is configured to track a specific identity (individual swappers), but a parallel execution path (the router) causes the checked value to diverge from the intended one (router address ≠ actual user). The mismatch is invisible at configuration time and persists silently.

---

### Impact Explanation

Any non-allowlisted user can swap in a pool that is intended to be restricted by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`). If the pool is designed to be private — e.g., institutional-only, KYC-gated, or rate-limited to specific counterparties — the bypass allows unrestricted arbitrage and toxic flow against LP positions, causing direct loss of LP principal. The allowlist invariant ("only approved addresses may swap") is broken for every pool that allowlists the router.

---

### Likelihood Explanation

Medium. The bypass requires the pool admin to add the router to the allowlist. This is a predictable operational step: a pool admin who wants allowlisted users to be able to use the standard router will add the router address. The mistake is non-obvious because the admin expects the router to act as a transparent forwarder of user identity, which it does not. No attacker privilege is required; any EOA can call the router.

---

### Recommendation

The `SwapAllowlistExtension` must check the economically relevant actor, not the intermediary. Two options:

1. **Forward the real caller in `extensionData`**: Have `MetricOmmSimpleRouter` encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check it when `sender` is a known router.
2. **Check `sender` and fall back to `extensionData`-decoded user**: If `sender` is an allowlisted router, decode the actual user from `extensionData` and apply the per-user check to that address instead.

Either approach closes the gap between the configured gate (individual users) and the checked identity (router).

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` in the `beforeSwap` slot.
2. Pool admin allowlists Alice: `setAllowedToSwap(pool, alice, true)`.
3. Pool admin allowlists the router so Alice can use it: `setAllowedToSwap(pool, router, true)`.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)`. Inside the pool, `msg.sender` = router.
6. Pool calls `_beforeSwap(router, ...)`. Extension checks `allowedSwapper[pool][router]` → `true` → passes.
7. Bob's swap executes in the restricted pool. The per-user allowlist is fully bypassed. [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-29)
```text
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
