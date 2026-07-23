### Title
`SwapAllowlistExtension` checks the router address as `sender` instead of the actual user, allowing any user to bypass the swap allowlist on curated pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the `pool.swap()` call. When `MetricOmmSimpleRouter` is used, `sender` is the router address, not the actual user. If the pool admin allowlists the router (the only way to enable router-based swaps on a curated pool), every user on the network can bypass the allowlist by routing through the router.

---

### Finding Description

The pool's `swap()` function passes `msg.sender` as the `sender` argument to `_beforeSwap()`, which forwards it to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` then encodes `sender` as the first argument in the call to each extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) calls `pool.swap()`, the pool receives `msg.sender = router`: [4](#0-3) 

So the extension evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][actualUser]`. The pool admin faces an impossible choice:

- **Do not allowlist the router** → no user can swap through the router, even individually allowlisted ones.
- **Allowlist the router** → every user on the network can bypass the allowlist by routing through the router.

This is structurally different from `DepositAllowlistExtension`, which correctly checks the `owner` parameter (the actual position owner), not `sender` (the LiquidityAdder intermediary): [5](#0-4) 

The pool's `addLiquidity` passes the actual position owner as `owner` and the LiquidityAdder as `sender`, so the deposit allowlist correctly gates on the economic actor. The swap allowlist has no equivalent "owner" parameter — it only has `sender` (the router) and `recipient` (the output destination, which may differ from the payer).

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of addresses (e.g., KYC'd counterparties, whitelisted market makers) is completely defeated once the router is allowlisted. Any unpermissioned user can call `MetricOmmSimpleRouter.exactInputSingle` with the curated pool address and execute swaps at oracle-anchored prices, receiving output tokens directly. This is a direct loss of curation policy and, depending on pool configuration, can result in LP value leakage to unpermissioned actors at oracle-fair prices that the pool admin intended to restrict.

---

### Likelihood Explanation

The router is the canonical, documented periphery path for swaps. Any pool admin who wants allowlisted users to be able to use the router (the normal UX) must allowlist the router, which immediately opens the pool to all users. The misconfiguration is the only viable operational path, making exploitation trivially reachable by any user who calls the router.

---

### Recommendation

The `beforeSwap` hook should not rely on `sender` (the direct caller of `pool.swap()`) to identify the economic actor. Two options:

1. **Check `recipient` as a proxy** — only valid if the pool admin's intent is to restrict who can receive output, not who pays.
2. **Require the actual user identity in `extensionData`** — the router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated change to the router and extension.

The deposit allowlist's pattern (checking `owner`, a parameter the pool itself controls and sets to the actual position owner) is the correct model. An analogous "payer" field would need to be threaded through the swap path to fix this properly.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is supposed to trade.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — required so Alice can use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: bob, ...})`.
5. The pool calls `extension.beforeSwap(router, bob, ...)`. The extension checks `allowedSwapper[pool][router]` → `true`. Bob's swap succeeds.
6. The allowlist is fully bypassed. [3](#0-2) [6](#0-5) [7](#0-6)

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
