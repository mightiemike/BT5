Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address as `sender` instead of end-user, enabling allowlist bypass for router-mediated swaps — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` as seen by the pool. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks whether the **router** is allowlisted rather than the actual end-user. A pool admin who allowlists the router to support standard UX inadvertently grants every user on the network the same swap permission, completely defeating the intended access control.

## Finding Description

**Confirmed call chain:**

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` — which is the router when called via `MetricOmmSimpleRouter.exactInputSingle` — as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // = router address when called via MetricOmmSimpleRouter
  recipient,
  ...
  extensionData
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)  // sender = router address
  )
);
```

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router — not the original user:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly with no mechanism to encode or forward the original `msg.sender` into `extensionData` or any other field:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
  );
```

The router stores the original `msg.sender` only in transient storage as the **payer** for the callback, not in any field visible to extensions. There is no path by which the extension can recover the true initiator.

The existing unit tests only exercise direct pool calls (`vm.prank(address(pool))`), never router-mediated calls, so the bypass is untested.

## Impact Explanation

A curated pool (KYC-only, institutional, or permissioned LP pool) deploys `SwapAllowlistExtension` to restrict swaps to approved addresses. To also support the standard periphery router for those approved users, the admin calls `setAllowedToSwap(pool, router, true)`. From that moment, **any** address — including completely unapproved users — can call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps against the pool at oracle-derived prices. The allowlist is entirely bypassed. LP funds are at direct risk because unauthorized traders can drain the pool's liquidity at the oracle mid-price. This constitutes a direct loss of user principal and broken core pool functionality (allowlist bypass / admin-boundary break).

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary documented user-facing entry point. A pool admin who wants allowlisted users to be able to use the router — the normal UX path — must allowlist the router; there is no other mechanism. This is a natural, expected configuration step, not an exotic edge case. Any pool that combines `SwapAllowlistExtension` with router support is vulnerable. The attacker requires no special privileges: any EOA can call `exactInputSingle` on the router.

## Recommendation

Pass the original end-user identity through the swap path rather than the immediate `msg.sender`. Two complementary fixes:

1. **Router-side**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` (or a dedicated field) before calling `pool.swap`, so extensions can recover the true initiator.
2. **Extension-side**: `SwapAllowlistExtension.beforeSwap` should decode and check the original user from `extensionData` when `sender` is a known router, or the pool should expose a dedicated `originator` field separate from `sender`.

A short-term mitigation: document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and revert in `beforeSwap` if `sender` is the router address, forcing direct-pool-only access for allowlisted pools.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as the `beforeSwap` extension.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps for allowlisted users.
3. A completely non-allowlisted `attacker` calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
4. The pool receives `msg.sender = router`, calls `_beforeSwap(router, ...)`.
5. The extension checks `allowedSwapper[pool][router] == true` → passes.
6. The attacker's swap executes at oracle price against the curated pool's LP funds.

Foundry test plan: extend `FullMetricExtension.t.sol` to deploy a `MetricOmmSimpleRouter`, allowlist the router via `swapExtension.setAllowedToSwap(address(pool), address(router), true)`, then call `router.exactInputSingle(...)` from an address that is **not** individually allowlisted and assert the swap succeeds (demonstrating the bypass). The existing `test_blocksSwapWhenSwapperNotAllowed` only pranks a direct pool caller and does not cover this path. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L55-61)
```text
  function test_blocksSwapWhenSwapperNotAllowed() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);

    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
