Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of originating user, enabling full allowlist bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps using the `sender` argument, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. Any pool admin who allowlists the router to enable router-mediated swaps for legitimate users inadvertently opens the pool to every caller, completely defeating the per-user access control the extension was deployed to enforce.

## Finding Description

`MetricOmmPool.swap()` passes its own `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the pool's `msg.sender` the router, not the originating user: [4](#0-3) 

The allowlist lookup therefore becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The pool admin faces an impossible choice: if the router is not allowlisted, every legitimate user who routes through the periphery is blocked; if the router is allowlisted, the allowlist is bypassed entirely for all callers.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly ignores the first (`sender`) argument and gates the explicit `owner` parameter, confirming this is a design flaw specific to `SwapAllowlistExtension`: [5](#0-4) 

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, whitelisted protocols, or institutional participants) can be fully bypassed by any unprivileged user routing through `MetricOmmSimpleRouter`. Every swap executed by an unauthorized user against a restricted pool is a direct policy violation: unauthorized price impact on LP positions, potential regulatory exposure, and loss of curated-pool economics. This constitutes broken core pool functionality causing loss of funds and an admin-boundary break where an unprivileged path bypasses the intended access control.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entrypoint. Any user who discovers that the router is allowlisted on a restricted pool can immediately exploit the bypass with a single `exactInputSingle` call. No admin action, no special token, and no timing dependency is required. The attack is repeatable indefinitely.

## Recommendation

`SwapAllowlistExtension.beforeSwap` should gate the originating user, not the direct caller of `pool.swap()`. Two complementary fixes:

1. **Pass the original user through the router**: `MetricOmmSimpleRouter` should encode `msg.sender` (the originating user) into `extensionData` for each hop. `SwapAllowlistExtension.beforeSwap` should decode and check that address instead of `sender`. This mirrors how `DepositAllowlistExtension` correctly gates `owner` (the explicit economic actor) rather than the caller.

2. **Alternatively, check `recipient` as a proxy**: if the pool's design intent is that the recipient is always the economic beneficiary, the extension could check `recipient` instead of `sender`. However, this is only correct if the router always sets `recipient` to the originating user, which holds for single-hop but not for multi-hop intermediate hops.

The cleanest fix is option 1.

## Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Admin calls setAllowedToSwap(pool, alice, true)  — only alice should trade.
3. Admin calls setAllowedToSwap(pool, router, true) — needed so alice can use the router.
4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
5. Router calls pool.swap(recipient=bob, ...) — pool's msg.sender = router.
6. _beforeSwap(sender=router, ...) → SwapAllowlistExtension checks allowedSwapper[pool][router] → true.
7. Bob's swap executes successfully despite not being in the allowlist.
```

A Foundry integration test can confirm this by deploying a pool with `SwapAllowlistExtension`, allowlisting only `alice` and the router, then calling `exactInputSingle` from an unprivileged `bob` address and asserting the call succeeds (no `NotAllowedToSwap` revert).

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
