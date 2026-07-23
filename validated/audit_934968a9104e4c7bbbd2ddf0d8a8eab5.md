Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of end user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. A pool admin who allowlists the router to support router-mediated swaps for their curated users inadvertently opens the gate to every user of the public router, completely defeating the allowlist.

## Finding Description

`SwapAllowlistExtension.beforeSwap` enforces the allowlist by checking the `sender` parameter passed from the pool: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards whatever `sender` the pool supplies directly to extensions without modification: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router the `msg.sender` of the pool. The end user's address is stored only in transient storage via `_setNextCallbackContext` for the payment callback — it is never forwarded to the pool or extension as the economic actor: [3](#0-2) 

The same pattern applies to `exactOutputSingle` and `exactInput`/`exactOutput` multi-hop paths.

The test suite confirms the binding: the allowlist is keyed to `callers[0]` (the direct pool caller / intermediary contract), not `users[0]` (the end user): [4](#0-3) 

This creates an irreconcilable dilemma for any pool admin deploying a curated pool with `SwapAllowlistExtension`:

| Admin action | Effect |
|---|---|
| Allowlist individual user addresses only | Allowlisted users **cannot** use the router (router address not allowlisted → revert) |
| Allowlist the router address to support router-mediated swaps | **Every** user of the public router bypasses the allowlist |

There is no configuration that achieves "only allowlisted users may swap, including through the router."

## Impact Explanation

Any unprivileged user can bypass the swap allowlist on a curated pool by routing through `MetricOmmSimpleRouter`. The allowlist is the sole access-control mechanism for such pools; bypassing it allows disallowed parties to trade against LP capital in ways the pool admin explicitly intended to prevent. This constitutes a direct, fund-impacting policy violation: unauthorized swaps execute against LP principal, and the pool admin has no recourse once the router is allowlisted.

## Likelihood Explanation

High. `MetricOmmSimpleRouter` is the primary user-facing swap entry point. Any pool admin who wants their allowlisted users to be able to use the router will allowlist the router address — a natural and expected operational step. Once the router is allowlisted, the bypass is trivially reachable by any public user with no special privileges, setup, or capital beyond the swap amount.

## Recommendation

The pool must forward the originating user identity to extensions, not just `msg.sender`. Two concrete approaches:

1. **Router-side**: Have the router pass the end user's address as part of `extensionData` (authenticated via a signature or trusted router flag), and have `SwapAllowlistExtension.beforeSwap` decode and verify it from `extensionData`.
2. **Pool-side**: Add an `originator` field to the swap call that the router populates with its `msg.sender` before calling the pool, and have the pool forward it as `sender` to extensions instead of its own `msg.sender`.

Note: `DepositAllowlistExtension` faces a structurally analogous issue — it checks `owner` (which any caller can set to an allowlisted address) rather than the payer who actually controls the tokens. [5](#0-4) 

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension`.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — Alice is the only intended swapper.
3. Alice calls `router.exactInputSingle(...)` — **reverts** because `allowedSwapper[pool][router] = false`.
4. Admin calls `setAllowedToSwap(pool, router, true)` to allow Alice to use the router.
5. Charlie (never allowlisted) calls `router.exactInputSingle(...)` — **succeeds** because `allowedSwapper[pool][router] = true`.
6. Charlie executes an unauthorized swap against LP capital on the curated pool.

Foundry test plan: extend `FullMetricExtensionTest`, deploy a second EOA as Charlie, call `swapExtension.setAllowedToSwap(address(pool), address(router), true)`, then call `router.exactInputSingle` from Charlie's address and assert it succeeds rather than reverting with `NotAllowedToSwap`.

### Citations

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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
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
