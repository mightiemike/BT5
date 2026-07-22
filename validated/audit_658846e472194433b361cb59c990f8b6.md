### Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Allowing Full Allowlist Bypass via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` — the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router to enable router-mediated swaps, every user on-chain can bypass the per-user allowlist. If the admin does not allowlist the router, allowlisted users are silently blocked from using the router.

### Finding Description

**Call chain:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle()` (or `exactInput`, `exactOutputSingle`, `exactOutput`).
2. The router calls `IMetricOmmPoolActions(pool).swap(recipient, zeroForOne, ...)`.
3. Inside `MetricOmmPool.swap()`, `_beforeSwap(msg.sender, ...)` is called with `msg.sender = router`. [1](#0-0) 

4. `ExtensionCalling._beforeSwap` forwards `sender = router` to every configured extension. [2](#0-1) 

5. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router address — not the end user. [3](#0-2) 

The allowlist mapping is keyed `allowedSwapper[pool][swapper]`. When the pool admin intends to gate individual users, they set entries like `allowedSwapper[pool][alice] = true`. But the extension receives `sender = router`, so it evaluates `allowedSwapper[pool][router]` — a completely different key. [4](#0-3) 

**Two broken outcomes result:**

- **Bypass path:** If the pool admin allowlists the router address (to permit router-mediated swaps), `allowedSwapper[pool][router] = true` passes for every caller of the router — including users who are not individually allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle()` and trade on a curated pool.
- **Broken functionality path:** If the admin does not allowlist the router, individually allowlisted users are silently blocked from using the router, even though they are permitted to trade.

The pool admin cannot simultaneously allow router-mediated swaps and enforce per-user curation. One of the two invariants must be sacrificed.

Note: `DepositAllowlistExtension` does **not** share this flaw — it checks `owner` (the position owner explicitly passed to `addLiquidity`), not `sender`. [5](#0-4) 

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd or whitelisted counterparties loses that guarantee the moment the router is allowlisted. Any user can call `MetricOmmSimpleRouter` and execute swaps against the pool's LP reserves, extracting value at oracle-anchored prices. LP principal is directly at risk because the pool was deployed under the assumption that only vetted counterparties would trade against it.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported periphery path for swaps. Pool admins who want to offer a usable UX will allowlist the router. The bypass is then reachable by any unprivileged user with a single `exactInputSingle` call. No special setup, flash loan, or privileged access is required.

### Recommendation

Pass the original end-user address through the callback data or a separate transient storage slot, and have the pool forward it as a distinct `originator` argument to extensions. Alternatively, `SwapAllowlistExtension` should check the payer stored in the router's transient context, or the pool should expose an `originator` field that the router populates before calling `swap`. The simplest fix is to have `MetricOmmSimpleRouter` pass `msg.sender` (the end user) inside `callbackData` and have the extension read it from there, or to add an `originator` parameter to the `beforeSwap` hook signature.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is KYC'd
  allowedSwapper[pool][router] = true         // admin enables router path
  charlie = unprivileged address (not allowlisted)

Attack:
  charlie calls MetricOmmSimpleRouter.exactInputSingle({
      pool: pool,
      tokenIn: token0,
      ...
  })

  → router calls pool.swap(recipient=charlie, ...)
  → pool calls _beforeSwap(sender=router, ...)
  → SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  → swap executes; charlie receives token1 from LP reserves

Result:
  charlie, who is not individually allowlisted, successfully trades
  against a pool that was supposed to be restricted to KYC'd users.
``` [6](#0-5) [7](#0-6) [3](#0-2)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
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
