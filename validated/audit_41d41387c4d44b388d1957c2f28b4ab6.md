Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of end-user, enabling full allowlist bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension` is intended to gate swaps by per-pool swapper address. However, when swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. The extension checks `allowedSwapper[pool][router]`, so any user who routes through the router bypasses the per-user allowlist entirely if the router address is allowlisted.

## Finding Description
`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, recipient, ...)` at line 230, forwarding its own `msg.sender` as `sender` to every configured extension. [1](#0-0) 

`MetricOmmSimpleRouter.exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` directly, making the pool's `msg.sender` the router address, not the end-user. [2](#0-1) 

`ExtensionCalling._beforeSwap` passes this router address as `sender` to every extension. [3](#0-2) 

`SwapAllowlistExtension.beforeSwap` then evaluates:
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```
where `msg.sender` is the pool and `sender` is the router. The check resolves to `allowedSwapper[pool][router]`. If the router is allowlisted, the gate passes for every caller regardless of their individual allowlist status. [4](#0-3) 

The `onlyPool` modifier in `BaseMetricExtension` only verifies the caller is a registered pool — it does not recover the original user identity. [5](#0-4) 

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to a specific set of addresses (e.g., KYC'd users, institutional market makers) is fully bypassed for any user who routes through `MetricOmmSimpleRouter`. The pool admin has no way to simultaneously (a) allow router-mediated swaps for allowlisted users and (b) block non-allowlisted users from the router, because the extension cannot distinguish the two cases — it only sees the router address. Unauthorized swaps on a restricted pool cause direct LP principal loss through adverse selection, price impact, or violation of the pool's intended trading regime. This constitutes a broken core pool access-control mechanism causing loss of funds. [6](#0-5) 

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary public entry point for swaps. A pool admin who wants allowlisted users to use the router UI must allowlist the router address. Once the router is allowlisted, the bypass is unconditional and requires no special privileges — any address can call `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` on the router. [7](#0-6) 

## Recommendation
Pass the original end-user identity through the swap call so extensions can gate on it. The simplest safe fix is to have `MetricOmmSimpleRouter` encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and verify it when the immediate `sender` is a known router. Alternatively, redesign the hook interface to carry an `originator` field distinct from `sender`, which the pool forwards alongside `sender` to all extensions. [8](#0-7) 

## Proof of Concept
```
1. Deploy a pool with SwapAllowlistExtension as a beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, alice, true)  — only alice is allowed.
3. Pool admin calls setAllowedToSwap(pool, router, true) — router allowlisted so alice can use the UI.
4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
5. Router calls pool.swap(recipient, ...) → pool's msg.sender = router.
6. Pool calls _beforeSwap(router, ...) → extension checks allowedSwapper[pool][router] = true → passes.
7. Bob's swap executes on the restricted pool, bypassing the per-user allowlist entirely.
``` [2](#0-1) [1](#0-0) [4](#0-3)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-41)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
  }
```
