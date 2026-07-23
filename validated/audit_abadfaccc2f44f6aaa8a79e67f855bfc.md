Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the end user, enabling full allowlist bypass through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]`, where `sender` is the pool's `msg.sender` at the time `swap` is called. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks whether the **router** is allowlisted rather than the **end user**. Any pool that allowlists the router (a prerequisite for router-mediated swaps) is immediately exploitable by any non-allowlisted address.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value unchanged to every registered extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender])
```

where `msg.sender` is the pool and `sender` is whatever address called `pool.swap`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the pool's `msg.sender`: [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`. In every case the router is the immediate caller of `pool.swap`, so `sender = address(router)`. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

For any curated pool to be usable via the router at all, the pool admin must call `setAllowedToSwap(pool, router, true)`. Once that entry is set, every address — including addresses the admin explicitly excluded — can bypass the allowlist by routing through `MetricOmmSimpleRouter`.

Contrast with `DepositAllowlistExtension.beforeAddLiquidity`, which correctly gates `owner` — an explicit parameter the caller supplies — rather than `sender`: [5](#0-4) 

`MetricOmmPool.addLiquidity` passes the caller-supplied `owner` separately from `msg.sender`, so the router can forward the actual end user as `owner` and the extension gates the correct actor: [6](#0-5) 

The swap path has no equivalent explicit-user parameter; `sender` collapses to the router's address on every router-mediated swap.

## Impact Explanation

A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict swaps to KYC'd or institutional counterparties. The admin must also allowlist `MetricOmmSimpleRouter` so permitted users can trade via the standard UI. Once the router is allowlisted, any unpermissioned address can call `MetricOmmSimpleRouter.exact*` and the extension passes because `allowedSwapper[pool][router] == true`. LP assets are transferred to non-permitted counterparties at oracle prices without the pool admin's consent — a direct loss of LP principal and complete failure of the curation-policy invariant.

**Severity: High** — direct loss of LP assets and complete access-control failure on every pool using `SwapAllowlistExtension` together with the router.

## Likelihood Explanation

The exploit requires no privileged action beyond the pool admin's own necessary setup step (allowlisting the router). Any pool that (1) uses `SwapAllowlistExtension` as a `beforeSwap` hook and (2) allowlists `MetricOmmSimpleRouter` so permitted users can trade via the standard UX is immediately exploitable by any address. This is the expected production configuration for a curated pool with a router-facing UI. No special knowledge, flash loans, or oracle manipulation is required.

## Recommendation

Gate the **economic actor**, not the immediate caller. The cleanest fix mirrors the deposit path: add an explicit `sender` parameter to `pool.swap` (analogous to `owner` in `addLiquidity`) so the router can forward `msg.sender` (the end user) as that argument. The extension then checks the user, not the router. Alternatively, require the router to embed the real user address in `extensionData` and have the extension decode it when the immediate caller is a known router, though this requires a trusted-router registry and is more complex.

## Proof of Concept

```solidity
// Pool admin setup (legitimate):
swapAllowlist.setAllowedToSwap(address(pool), address(router), true); // router must be allowed
swapAllowlist.setAllowedToSwap(address(pool), alice, true);           // alice is a permitted trader
// bob is NOT added to the allowlist

// Attack — bob bypasses the allowlist:
// pool.msg.sender == router → extension checks allowedSwapper[pool][router] == true → passes
router.exactInputSingle(
    ExactInputSingleParams({
        pool: address(pool),
        recipient: bob,
        zeroForOne: true,
        amountIn: 1_000e18,
        amountOutMinimum: 0,
        priceLimitX64: type(uint128).max,
        deadline: block.timestamp,
        tokenIn: token0,
        extensionData: ""
    })
);
// swap succeeds; bob receives tokens despite never being allowlisted
// LP assets transferred to a non-permitted counterparty at oracle price
```

`beforeSwap` receives `sender = address(router)`, finds `allowedSwapper[pool][router] == true`, and passes. Bob's swap executes, extracting LP value without authorization.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
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
