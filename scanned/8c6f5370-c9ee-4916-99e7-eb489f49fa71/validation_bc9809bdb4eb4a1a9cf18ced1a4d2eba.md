### Title
SwapAllowlistExtension Checks Router Address as `sender`, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of `MetricOmmPool.swap`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. A pool admin who allowlists the router address (the only way to support router-mediated swaps for their intended users) inadvertently opens the gate to every user on the internet, completely defeating the allowlist.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that exact value against the per-pool allowlist: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` the pool sees: [3](#0-2) 

There is no mechanism in the router to forward the original caller's identity to the pool or to the extension. The extension receives `sender = address(router)` for every router-mediated swap, regardless of who called the router.

This creates an inescapable dilemma for any pool admin who deploys a curated pool with `SwapAllowlistExtension`:

- **Option A – allowlist only end-user addresses**: Allowlisted users cannot use the router (router address is not allowlisted → `NotAllowedToSwap`). The supported periphery path is broken for the intended users.
- **Option B – allowlist the router address**: Every user on the internet can call `router.exactInputSingle(...)` and the extension sees `sender = router`, which is allowlisted. The allowlist is completely bypassed.

The `generate_scanned_questions.py` audit target explicitly flags this concern: [4](#0-3) 

The code contains no forwarding mechanism, no `msg.sender` relay, and no extension-data convention that would let the extension recover the true end-user identity.

### Impact Explanation

A curated pool (e.g., KYC-gated, institution-only, or regulatory-restricted) that uses `SwapAllowlistExtension` and allowlists the router to support normal user flows is fully open to any unprivileged address. The attacker receives the same swap execution as an allowlisted user: correct oracle pricing, correct token settlement, and no additional cost beyond gas. This is a direct policy bypass with fund-impacting consequences: the pool's LP positions are exposed to counterparties the pool admin explicitly intended to exclude.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical supported swap path documented in the README and referenced throughout the periphery. Any pool admin who wants their allowlisted users to be able to use the standard router must allowlist the router address. The bypass is then reachable by any unprivileged caller with no special setup, no privileged role, and no non-standard token behavior.

### Recommendation

The `sender` identity must be the end user, not the router. Two complementary fixes:

1. **Router-level**: `MetricOmmSimpleRouter` should forward the original `msg.sender` through `extensionData` (or a dedicated field) so extensions can recover the true caller.
2. **Extension-level**: `SwapAllowlistExtension.beforeSwap` should decode the true caller from `extensionData` when `sender` is a known router, or the pool/router architecture should adopt a forwarded-sender convention (similar to ERC-2771 meta-transactions) so the extension always sees the economic actor.

Until fixed, pool admins should not allowlist the router address and should document that router-mediated swaps are unsupported on allowlisted pools.

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension as beforeSwap extension.
2. Pool admin calls setAllowedToSwap(pool, allowedUser, true).
3. Pool admin calls setAllowedToSwap(pool, address(router), true)
   — required so allowedUser can use the standard router.
4. bannedUser (not in allowlist) calls:
     router.exactInputSingle(ExactInputSingleParams{
       pool: pool,
       recipient: bannedUser,
       zeroForOne: true,
       amountIn: X,
       ...
     });
5. Router calls pool.swap(...) → pool passes msg.sender = router to _beforeSwap.
6. SwapAllowlistExtension checks allowedSwapper[pool][router] → true → passes.
7. bannedUser receives swap output. Allowlist completely bypassed.
``` [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** generate_scanned_questions.py (L658-663)
```python
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```
