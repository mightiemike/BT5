### Title
SwapAllowlistExtension gates the router address instead of the end-user, allowing any unprivileged caller to bypass the swap allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the end user. If the pool admin adds the router to the allowlist (a necessary step for any allowed user to use the router), every unprivileged address can bypass the per-user allowlist by routing through the router.

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces the allowlist by checking `sender` against `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the argument forwarded by the pool: [1](#0-0) 

The pool always passes its own `msg.sender` as the `sender` argument to the extension: [2](#0-1) [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [4](#0-3) 

At the pool, `msg.sender` is the router. The pool passes `msg.sender = router` as `sender` to `_beforeSwap`, so the extension evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][end_user]`.

The pool admin must add the router to the allowlist for any allowed user to use the router. Once the router is allowlisted, the check `allowedSwapper[pool][router] == true` passes for every caller of the router, regardless of whether that caller is individually allowed.

### Impact Explanation

A curated pool (e.g., KYC-gated, institutional-only) that uses `SwapAllowlistExtension` to restrict trading to specific addresses loses its access control entirely for router-mediated swaps. Any unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` and trade on the pool as if they were allowlisted. This constitutes a curation failure and a broken core pool functionality: the pool's configured guard is silently bypassed on every router path.

### Likelihood Explanation

The scenario is realistic and requires no special privileges:
1. Pool admin deploys a pool with `SwapAllowlistExtension` and adds specific user addresses.
2. Pool admin adds the router to the allowlist so that allowed users can use the standard periphery.
3. Any unprivileged address calls the router — the extension sees `sender = router`, which is allowlisted, and the swap proceeds.

The trigger is a normal, permissionless call to the public router. No admin cooperation or malicious setup is required beyond the admin's own legitimate configuration step.

### Recommendation

The extension must identify the true end-user, not the intermediary. Two options:

1. **Check `sender` only when it is not a known router**: Maintain a registry of trusted routers; when `sender` is a router, revert (routers cannot be allowlisted as a proxy for all users).
2. **Require routers to forward the original caller**: Add an optional `bytes calldata extensionData` field that routers populate with the original `msg.sender`, and verify that value against the allowlist. The pool already forwards `extensionData` to extensions unchanged.

The analogous fix for `DepositAllowlistExtension` is not needed because it already checks `owner` (the position recipient) rather than `sender` (the intermediary), correctly gating the economically relevant actor.

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension; add alice (allowed) and router (needed for alice to use router) to allowedSwapper[pool].
2. bob (not in allowlist) calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
3. Router calls pool.swap(recipient, zeroForOne, ..., extensionData).
4. Pool calls _beforeSwap(msg.sender=router, ...).
5. Extension evaluates: allowedSwapper[pool][router] == true → passes.
6. bob's swap executes on the curated pool despite not being individually allowlisted.
``` [1](#0-0) [5](#0-4) [6](#0-5)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L217-240)
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
