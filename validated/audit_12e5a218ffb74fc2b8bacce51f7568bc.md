Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded from the pool, which is the pool's own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`. Any pool admin who allowlists the router to support standard UX simultaneously opens the gate to every address on earth, completely defeating the curation policy.

## Finding Description

**Root cause — wrong actor identity propagated through the call chain:**

`MetricOmmPool.swap` passes its own `msg.sender` verbatim as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` encodes that value and dispatches it to every configured extension unchanged. `SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender`.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly with no intermediary, making the router the `msg.sender` the pool observes:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
    );
```

The same substitution occurs in `exactInput` (intermediate hops use `address(this)` = router) and in `_exactOutputIterateCallback` (recursive hops use the previous pool as `msg.sender`).

**Exploit path:**
1. Pool admin deploys pool with `SwapAllowlistExtension` as `beforeSwap` hook.
2. Admin allowlists `userA` (legitimate) and `router` (to support standard UX).
3. `userB` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle` targeting the curated pool.
4. Router calls `pool.swap(...)` — pool sees `msg.sender = router`.
5. Extension checks `allowedSwapper[pool][router]` → `true` → swap executes.
6. `userB` receives output tokens; `NotAllowedToSwap` is never reverted.

**Existing guards are insufficient:** The extension has no mechanism to distinguish the real initiator from the immediate caller. The `recipient` field is also attacker-controlled and cannot serve as a reliable identity anchor. The unit tests in `SwapAllowlistSubExtension.t.sol` only exercise the direct-pool path (`vm.prank(address(pool)); extension.beforeSwap(swapper, ...)`), and `FullMetricExtension.t.sol` calls the pool through a `TestCaller` wrapper rather than through `MetricOmmSimpleRouter`, leaving the router bypass path entirely untested and undetected.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` is a curated pool — the admin intends to restrict trading to specific addresses (e.g., KYC-verified counterparties, institutional desks, whitelisted market makers). The allowlist is the sole on-chain enforcement of that policy. Once the router is allowlisted (the natural operational state for any pool supporting standard UX), any address can call `MetricOmmSimpleRouter.exactInputSingle` and execute a full swap against the pool's liquidity at oracle-derived prices. LP funds are directly at risk because the pool's bid/ask spread and liquidity depth were calibrated for a controlled counterparty set. This constitutes a direct loss of LP assets and broken core pool functionality (the allowlist guard becomes vacuous), meeting the High severity threshold.

## Likelihood Explanation

- `MetricOmmSimpleRouter` is a public, permissionless periphery contract — no special role or token is required to call it.
- The bypass requires zero privileged access: any EOA or contract can call `exactInputSingle` with the target pool address.
- The precondition (router is allowlisted) is the natural operational state for any pool that wants to support standard UX; without it, even legitimate allowlisted users cannot use the router.
- No admin action is needed to trigger the bypass; it is always active once the router is allowlisted.

## Recommendation

`SwapAllowlistExtension.beforeSwap` must gate the economically relevant actor — the end user — not the immediate `msg.sender` of the pool's `swap` call. The preferred fix is to mirror the pattern used by `MetricOmmPoolLiquidityAdder`, which stores the payer in transient storage before calling the pool and reads it in the callback. The router should store `msg.sender` in a dedicated transient storage slot before calling `pool.swap`, and the extension should read that slot to identify the real swapper. This requires a coordinated change to `MetricOmmSimpleRouter` and `SwapAllowlistExtension`. Alternatively, document that pools using `SwapAllowlistExtension` must never allowlist any router or intermediary and that all allowlisted users must call `pool.swap` directly — but this breaks standard UX and is operationally fragile.

## Proof of Concept

```solidity
// Setup:
// - pool has SwapAllowlistExtension configured as beforeSwap hook
// - pool admin allowlists: userA (legitimate), router (for UX)
// - userB is NOT allowlisted

MetricOmmSimpleRouter.ExactInputSingleParams memory params = MetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(curated_pool),
    tokenIn: token0,
    recipient: userB,
    zeroForOne: true,
    amountIn: 1_000e18,
    amountOutMinimum: 0,
    priceLimitX64: type(uint128).max,
    deadline: block.timestamp + 1,
    extensionData: ""
});

// userB calls the public router — no allowlist check on userB
vm.prank(userB);
router.exactInputSingle(params);
// pool.swap is called with msg.sender = router
// SwapAllowlistExtension checks allowedSwapper[pool][router] → true
// swap executes; userB receives output tokens
// NotAllowedToSwap is never reverted
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-125)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```

**File:** metric-periphery/test/extensions/SwapAllowlistSubExtension.t.sol (L26-38)
```text
  function test_revertsWhenSwapperNotAllowed() public {
    vm.prank(address(pool));
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    extension.beforeSwap(swapper, address(0), false, 0, 0, 0, 0, 0, "");
  }

  function test_passesWhenSwapperAllowed() public {
    vm.prank(admin);
    extension.setAllowedToSwap(address(pool), swapper, true);

    vm.prank(address(pool));
    extension.beforeSwap(swapper, address(0), false, 0, 0, 0, 0, 0, "");
  }
```

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L55-74)
```text
  function test_blocksSwapWhenSwapperNotAllowed() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);

    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }

  function test_blocksDepositWhenDepositorNotAllowed() public {
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToDeposit.selector);
    _addLiquidity(0, -5, 4, 10_000, EXTENSION_TEST_SALT);
  }

  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
