Audit Report

## Title
`SwapAllowlistExtension` gates the router address instead of the originating user when swaps route through `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the direct caller of `pool.swap()`. When any swap is routed through `MetricOmmSimpleRouter`, the router is that direct caller, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][end_user]`. This mismatch either silently opens the allowlist to all router users (if the router is allowlisted) or permanently breaks the router for all individually-allowlisted users.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that `sender` value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on that `sender`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is `msg.sender` when it calls `pool.swap`: [4](#0-3) 

So the extension receives `sender = router address`, and the check becomes `allowedSwapper[pool][router]` — never `allowedSwapper[pool][end_user]`. The same applies to `exactOutputSingle` (L135-137), `exactInput` (L103-112, where intermediate hops use `address(this)`), and `exactOutput` (L165-181).

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the second argument, explicitly set to the LP position owner) rather than `sender` (the direct caller), because `addLiquidity` separates the two roles: [5](#0-4) 

The swap interface has no equivalent originating-user field — `pool.swap` only takes `recipient`, not a separate `swapper`/`originator` argument — so there is no on-chain mechanism for the extension to recover the true end user.

## Impact Explanation

**Scenario A — Allowlist bypass (Critical/High):** A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict swaps to KYC'd addresses. To let those users use the router, the admin adds the router to the allowlist (`setAllowedToSwap(pool, router, true)`). Because `MetricOmmSimpleRouter` is a public, permissionless contract, any address can call `exactInputSingle` through it. The extension sees `sender = router` and passes the check for every caller, completely defeating per-user curation. All user principal flowing through the pool is accessible to unapproved swappers — direct loss of curation policy and user funds.

**Scenario B — Broken core swap functionality (High):** A pool admin allowlists individual user addresses for direct `pool.swap()` calls. Those users cannot swap through the router because the extension sees `sender = router` (not allowlisted) and reverts with `NotAllowedToSwap`. The router — the primary production entry point — is permanently broken for all allowlisted users on that pool, constituting broken core swap functionality.

Both scenarios meet Sherlock thresholds: Scenario A is a direct allowlist bypass enabling unauthorized principal flow; Scenario B renders the primary swap entry point unusable for the intended user set.

## Likelihood Explanation

Any pool that deploys `SwapAllowlistExtension` and expects users to interact via `MetricOmmSimpleRouter` is affected — this is the expected production configuration. No privileged access is required: any public user calling the router reaches the vulnerable path. The trigger is deterministic and repeatable on every router-mediated swap against an allowlisted pool.

## Recommendation

The swap interface must be extended to carry the originating user separately from the direct caller. The preferred fix is to add a `swapper` (or `originator`) field to `IMetricOmmPoolActions.swap` that the router populates with `msg.sender` before calling the pool, and the pool forwards to `_beforeSwap` in place of (or alongside) `msg.sender`. `SwapAllowlistExtension.beforeSwap` would then check this explicit originator field. This mirrors how `addLiquidity` already separates `sender` (direct caller) from `owner` (LP position owner), allowing `DepositAllowlistExtension` to gate the correct actor.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - setAllowedToSwap(pool, alice, true)   // alice is the intended curated user
  - setAllowedToSwap(pool, router, false) // router is not explicitly allowlisted

Scenario B — broken functionality:
  1. Alice calls router.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient=alice, ...)  →  pool sees msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. Extension checks allowedSwapper[pool][router] == false  →  revert NotAllowedToSwap
  5. Alice cannot use the router despite being allowlisted

Scenario A — bypass (admin tries to fix Scenario B):
  1. Admin sets setAllowedToSwap(pool, router, true) to let alice use the router
  2. Bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  3. Router calls pool.swap(recipient=bob, ...)  →  pool sees msg.sender = router
  4. Extension checks allowedSwapper[pool][router] == true  →  check passes
  5. Bob's swap executes despite not being on the allowlist

Foundry test plan:
  - Deploy SwapAllowlistExtension, pool, and MetricOmmSimpleRouter
  - allowedSwapper[pool][alice] = true; allowedSwapper[pool][router] = false
  - vm.prank(alice); router.exactInputSingle(...)  →  assert revert NotAllowedToSwap
  - allowedSwapper[pool][router] = true
  - vm.prank(bob); router.exactInputSingle(...)  →  assert swap succeeds (bypass confirmed)
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
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
