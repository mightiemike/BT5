Audit Report

## Title
Router-Mediated Swaps Substitute Router Address for Actual Swapper in `SwapAllowlistExtension.beforeSwap` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter` mediates a swap, the router's address — not the originating user's address — is passed as `sender`. Any pool that allowlists the router to support normal UX silently opens its per-user allowlist to every unprivileged caller who routes through the router.

## Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` encodes that `sender` and forwards it verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks the allowlist keyed on that `sender`: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()`, the pool's `msg.sender` is the **router contract**, not the originating EOA. The router stores the originating user only in transient callback context for payment purposes, but never passes it as a parameter to `pool.swap()`: [4](#0-3) 

Therefore `sender` arriving at `beforeSwap` is always the router address for any router-mediated swap. The hook evaluates `allowedSwapper[pool][router]` — a single boolean — instead of `allowedSwapper[pool][actual_user]`. The same substitution applies to `exactOutputSingle`, `exactInput`, and `exactOutput`.

The `whenNotPaused` modifier on `swap()` reverts before `_beforeSwap` is reached when the pool is paused, so the paused state is irrelevant to this bypass: [5](#0-4) 

## Impact Explanation

`SwapAllowlistExtension` is documented as gating `swap` by swapper address per pool. When the router is involved, the identity it checks is the router's, not the actual swapper's. Any pool that (a) uses this extension to restrict swaps to a curated set of addresses and (b) also allowlists the router to support normal UX is silently open to any unprivileged user. This constitutes broken core functionality of the extension — the per-user access control it is designed to enforce is entirely lost through the router path. [6](#0-5) 

## Likelihood Explanation

The router is the standard user-facing entry point for swaps. A pool admin who deploys `SwapAllowlistExtension` to restrict access but also wants users to be able to use the router will naturally allowlist the router — the code gives no indication that doing so collapses the per-user gate. The misconfiguration is easy to make and hard to detect without auditing the identity substitution across the call boundary. All four router entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) are affected. [7](#0-6) 

## Recommendation

The router should forward the originating user's address to the pool so extensions can gate on the real actor. One approach: add an explicit `originator` parameter to `pool.swap()` that the router sets to `msg.sender` before calling, similar to how Uniswap v4 passes `msgSender` through the unlock/callback boundary. Alternatively, `SwapAllowlistExtension.beforeSwap` could decode an originator address from `extensionData` when `sender` is a known router, and check that decoded address instead.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowlisted.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use it.
4. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(...)` with `msg.sender = router`.
6. `beforeSwap` receives `sender = router`; checks `allowedSwapper[pool][router] == true` → passes.
7. Bob's swap executes successfully despite not being individually allowlisted.

Direct call by Bob (`pool.swap(...)` directly) correctly reverts because `allowedSwapper[pool][bob] == false`. The bypass is exclusive to the router path. [8](#0-7)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-224)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
```

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-13)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
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
