Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist via the Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. A pool admin who allowlists the router to support standard UX inadvertently opens the gate for every user on-chain, completely defeating the allowlist.

## Finding Description
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the calling pool and `sender` is the address passed through from `MetricOmmPool.swap()`. [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards the `sender` parameter unchanged to every configured extension via `_callExtensionsInOrder`. [2](#0-1) 

In `MetricOmmSimpleRouter.exactInputSingle`, the actual user's address (`msg.sender`) is stored only in transient storage via `_setNextCallbackContext` for the payment callback. The `pool.swap()` call passes no user identity — the router itself becomes `msg.sender` to the pool, and thus `sender` in `beforeSwap`. [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — none forward the originating user's address to the pool. [4](#0-3) [5](#0-4) 

The result is an impossible choice: not allowlisting the router breaks UX for legitimate users; allowlisting the router grants every on-chain address the ability to bypass the per-user restriction by routing through `MetricOmmSimpleRouter`.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` is intended to be a permissioned venue restricted to specific counterparties. Once the router is allowlisted, any unprivileged address can trade on the pool by routing through `MetricOmmSimpleRouter`. This breaks the core pool invariant that only approved actors may swap, exposes LP funds to toxic flow the allowlist was designed to block, and constitutes a direct bypass of a configured protection hook. Severity: **High** — broken core pool functionality / allowlist guard fails open for all router-mediated swaps. [6](#0-5) 

## Likelihood Explanation
`MetricOmmSimpleRouter` is the canonical, publicly deployed periphery entry point. Any user who discovers the pool is allowlist-gated can trivially route through the router instead of calling the pool directly. No privileged access, no special tokens, and no admin cooperation is required beyond the pool admin having allowlisted the router for legitimate users — a necessary operational step. [7](#0-6) 

## Recommendation
1. **Pass the originating user through the router.** Add a `swapper` field to each router swap call and forward it to the pool via `extensionData`. `SwapAllowlistExtension.beforeSwap` should decode and check that field instead of (or in addition to) `sender`.
2. **Alternatively, check `sender` against the router and then require a user-level proof in `extensionData`** (e.g., a signed permit or an on-chain registry lookup keyed by the actual EOA).
3. **Document the limitation clearly** in `SwapAllowlistExtension` NatSpec: the current `sender` check is only meaningful for direct pool calls; router-mediated swaps present the router address as `sender`. [8](#0-7) 

## Proof of Concept
```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, alice, true)       // alice is the only allowed swapper
  admin calls setAllowedToSwap(pool, router, true)      // router allowlisted so alice can use it

Attack (bob, not allowlisted):
  bob calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)            // msg.sender to pool = router
    → pool calls _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension checks allowedSwapper[pool][router] == true
    → check PASSES
    → bob's swap executes on the allowlisted pool

Verification:
  bob calls pool.swap(...) directly
    → pool calls _beforeSwap(sender=bob, ...)
    → SwapAllowlistExtension checks allowedSwapper[pool][bob] == false
    → reverts NotAllowedToSwap                         // direct call correctly blocked

  Conclusion: router path bypasses the per-user allowlist entirely.
``` [1](#0-0) [3](#0-2)

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L135-137)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```
