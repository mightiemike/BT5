Looking at the extension guard system, I need to trace the exact actor identity that each allowlist hook checks, and whether that identity is preserved when swaps route through `MetricOmmSimpleRouter`.

**Key code path:**

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that `sender` argument verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` — `msg.sender` is the pool (correct), `sender` is whoever called the pool: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly — so the pool's `msg.sender` is the **router**, not the originating user: [4](#0-3) 

The router stores the real user only in transient callback context (for payment), never forwarding it to the pool or extension: [5](#0-4) 

`BaseMetricExtension.onlyPoolAdmin` always fetches the current admin live from the factory — so admin changes are respected: [6](#0-5) 

`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` (the position owner, explicitly passed), not `sender` — so the deposit allowlist correctly gates the economic actor regardless of who pays: [7](#0-6) 

The swap allowlist has no equivalent — it only sees the direct pool caller.

---

### Title
`SwapAllowlistExtension` gates the direct pool caller (`sender`) instead of the originating user, enabling per-user allowlist bypass through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of `MetricOmmPool.swap`. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, not the user. A pool admin who allowlists the router to enable router-mediated swaps inadvertently grants swap access to every user who can call the router, bypassing the per-user restriction entirely.

### Finding Description
`MetricOmmPool.swap` unconditionally passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // always the direct pool caller
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct); `sender` is whoever called the pool. When `MetricOmmSimpleRouter.exactInputSingle` is used, the router calls `pool.swap(...)`, so `sender = router`. The extension checks `allowedSwapper[pool][router]`, never seeing the originating user.

A pool admin who wants to enable router-mediated swaps for their curated pool will call `setAllowedToSwap(pool, router, true)`. From that moment, any address — including addresses the admin explicitly never allowlisted — can bypass the restriction by routing through the router. The admin's intent (per-user curation) is silently replaced by open access for all router callers.

This is structurally identical to the seeded bug: a privileged identity (old operator / router) is stored or checked at action time rather than the current authoritative identity (current operator / actual user), allowing the stale or wrong actor to exercise powers that should have been revoked or never granted.

Note: `SwapAllowlistExtension.beforeSwap` also drops the `onlyPool` modifier present on the base class override, but this does not create an independent bypass because calling it directly uses the caller's address as the pool key, for which no allowlist entries exist.

`DepositAllowlistExtension` does not share this flaw: it checks `owner` (the position owner, explicitly passed by the pool), so the deposit allowlist correctly gates the economic actor regardless of who the payer is.

### Impact Explanation
Any user can bypass a curated pool's swap allowlist by routing through `MetricOmmSimpleRouter` once the router is allowlisted. The allowlist — the pool admin's primary tool for restricting who may trade — is rendered ineffective. Depending on the pool's purpose (regulatory compliance, institutional-only access, beta testing), this constitutes a broken core access-control invariant with direct fund-flow consequences: disallowed users execute swaps and receive token output from the pool.

### Likelihood Explanation
The trigger requires the pool admin to allowlist the router. This is a natural, expected operational step: without it, allowlisted users cannot use the router at all (their EOA address is not the pool's `msg.sender`), forcing them to interact directly with the pool and losing all router conveniences (deadline checks, slippage guards, multi-hop). A pool admin who deploys a `SwapAllowlistExtension` and also wants router support will almost certainly allowlist the router, unknowingly opening the bypass.

### Recommendation
The extension must gate the originating user, not the direct pool caller. Two approaches:

1. **Pass the real user via `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires the extension to trust the router's encoding, which reintroduces a trust assumption.

2. **Structural fix — expose originator in the pool's hook arguments**: Add an explicit `originator` field to the `beforeSwap` hook signature (distinct from `sender`), populated by the pool as `msg.sender` for direct calls and by the router as the real user for router calls. This is the cleanest fix but requires a protocol-level interface change.

Until fixed, document clearly that allowlisting the router is equivalent to `setAllowAllSwappers(pool, true)` and that per-user curation is only enforceable for direct pool calls.

### Proof of Concept
```solidity
// 1. Pool deployed with SwapAllowlistExtension; only alice is intended to swap.
swapExtension.setAllowedToSwap(pool, alice, true);

//

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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L31-35)
```text
  modifier onlyPoolAdmin(address pool_) {
    address poolAdmin = IMetricOmmPoolFactory(FACTORY).poolAdmin(pool_);
    if (msg.sender != poolAdmin) revert OnlyPoolAdmin(pool_, msg.sender, poolAdmin);
    _;
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
