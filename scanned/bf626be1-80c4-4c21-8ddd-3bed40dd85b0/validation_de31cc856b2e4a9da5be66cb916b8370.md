### Title
`SwapAllowlistExtension` Gates Router Address Instead of Real Swapper, Enabling Allowlist Bypass via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the actual user. The extension therefore checks whether the router is allowlisted, not whether the individual user is allowlisted. Any user can bypass a curated pool's per-user swap allowlist by routing through the public router.

### Finding Description

The pool's `swap` function passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the immediate caller of `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly with no forwarding of the original user address: [4](#0-3) 

The pool therefore passes `address(router)` as `sender` to the extension. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates two mutually exclusive failure modes:

1. **Allowlist bypass**: If the pool admin allowlists the router (the natural step to enable router-mediated swaps for their allowlisted users), every unprivileged user can bypass the per-user gate by routing through the public router. The router is a single shared address; allowlisting it opens the gate for all callers.

2. **Broken core functionality**: If the pool admin does not allowlist the router, even individually allowlisted users cannot execute swaps through the router. The only usable path is a direct `pool.swap()` call, which requires the caller to implement `IMetricOmmSwapCallback` themselves.

By contrast, `DepositAllowlistExtension` correctly gates by `owner` (the position owner), which is an explicit argument that the liquidity adder preserves end-to-end: [5](#0-4) 

The swap extension has no equivalent mechanism to recover the real user identity when the router is the immediate caller.

### Impact Explanation

A pool admin configures `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties). To allow those users to trade through the supported periphery router, the admin allowlists the router address. Any unprivileged user then calls `router.exactInputSingle` or `router.exactInput` targeting that pool. The extension sees `sender = router`, which is allowlisted, and the swap proceeds. The curation boundary is fully bypassed with no privileged access required. The attacker trades on a pool they were explicitly excluded from, causing direct policy violation and potential LP value loss on a pool designed for restricted counterparties.

### Likelihood Explanation

High. `MetricOmmSimpleRouter` is the primary supported swap entrypoint for EOAs. Any pool admin who deploys a curated pool and wants their allowlisted users to use the router will naturally allowlist the router address. The bypass is then reachable by any user with zero cost beyond gas. Even without the admin allowlisting the router, the broken-functionality path (allowlisted users cannot use the router) is a guaranteed consequence of the current design for every curated pool.

### Recommendation

The extension must check the **economic actor**, not the immediate caller. Two options:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between the router and the extension.

2. **Mirror the deposit allowlist pattern**: Add a `recipient` or `originator` field to the swap hook signature that the pool populates from a trusted source, analogous to how `owner` is passed separately from `sender` in the liquidity hooks.

3. **Gate on `recipient` as a proxy**: For single-hop swaps the recipient is often the real user. This is imprecise for multi-hop paths but better than gating on the router.

The cleanest fix is option 1: the router encodes `abi.encode(msg.sender)` into `extensionData`, and `SwapAllowlistExtension.beforeSwap` decodes and checks that address when `sender` is a known router.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, alice, true)       // alice is KYC'd
  admin calls setAllowedToSwap(pool, address(router), true) // enable router path

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

Execution trace:
  router.exactInputSingle(...)
    → pool.swap(bob, ...)          // msg.sender = router
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ← passes
      → swap executes, bob receives tokens

Result: bob trades on a pool he was explicitly excluded from.
        The per-user allowlist is fully bypassed.
``` [3](#0-2) [6](#0-5) [7](#0-6)

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
