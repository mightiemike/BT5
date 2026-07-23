Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Originating User, Allowing Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which resolves to `msg.sender` inside `MetricOmmPool.swap()`. When `MetricOmmSimpleRouter` mediates the swap, that `msg.sender` is the router contract, not the originating user. Any admin who allowlists the router to enable router-mediated swaps for their curated users simultaneously opens the pool to every unprivileged user who can call the public router, silently breaking the per-user allowlist invariant.

## Finding Description

`SwapAllowlistExtension.beforeSwap()` performs the check:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct pool-namespacing) and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`, which is `msg.sender` inside `MetricOmmPool.swap()`:

```solidity
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap()
  recipient, ...
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` passes this value directly as the `sender` argument to the extension hook: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()`, the pool's `msg.sender` is the **router**, not the originating user:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [4](#0-3) 

The original user's address is stored only in transient callback context for payment settlement and is never forwarded to the extension hook. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][originalUser]`.

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Attack path:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to KYC'd/curated users.
2. Admin allowlists specific users: `allowedSwapper[pool][userA] = true`.
3. Admin allowlists the router so allowlisted users can use the standard periphery: `allowedSwapper[pool][router] = true`.
4. Non-allowlisted `userC` calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap()` with `msg.sender = router`.
6. Extension checks `allowedSwapper[pool][router]` → `true` → passes.
7. `userC` successfully swaps in the restricted pool, bypassing the per-user allowlist entirely.

Existing guards are insufficient: `BaseMetricExtension` correctly namespaces by pool via `msg.sender`, but there is no mechanism to recover the originating user once the router is the immediate caller.

## Impact Explanation

The swap allowlist is the sole mechanism for curated/compliant pools to restrict who may trade. When the router is allowlisted (a necessary operational step for any admin who wants their allowlisted users to use the standard periphery), the allowlist silently fails open for all router-mediated swaps. This constitutes an admin-boundary break via an unprivileged path: any public user can bypass the access-control invariant of a restricted pool by routing through `MetricOmmSimpleRouter`. The wrong value is the extension decision (`allowedSwapper[pool][router]` evaluated instead of `allowedSwapper[pool][originalUser]`), causing `NotAllowedToSwap` to never revert for router-mediated calls from non-allowlisted users.

## Likelihood Explanation

The trigger condition — admin allowlisting the router — is a natural and expected configuration. Any admin who wants their allowlisted users to use the standard periphery must allowlist the router. The `SwapAllowlistExtension` provides no warning or mechanism to avoid this. The router is a public, permissionless contract callable by anyone. The condition is reachable by any unprivileged user with no special privileges required.

## Recommendation

The extension must gate by the originating user, not the immediate caller. Two viable approaches:

1. **Pass originating user through `extensionData`**: Have the router encode `msg.sender` into `extensionData` and have the extension decode and verify it (requires a trusted-router check in the extension to prevent spoofing).
2. **Add an `originator` field to the swap interface**: Mirror the deposit pattern where `owner` is explicitly separated from `sender`. The `DepositAllowlistExtension` correctly avoids this problem by checking `owner` (explicitly separated from `sender`):

```solidity
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
``` [6](#0-5) 

The swap path lacks an equivalent separation. Adding an `originator` field to `IMetricOmmExtensions.beforeSwap` that the pool passes through to extensions would allow the extension to check the true economic actor.

## Proof of Concept

```solidity
// Pool configured with SwapAllowlistExtension
// Admin allowlists userA and the router
swapExt.setAllowedToSwap(address(pool), address(router), true);
swapExt.setAllowedToSwap(address(pool), userA, true);
// userB is NOT allowlisted

// Direct swap by userB → correctly reverts
vm.prank(userB);
pool.swap(...); // reverts NotAllowedToSwap ✓

// Router-mediated swap by userB → incorrectly succeeds
vm.prank(userB);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    ...
}));
// Extension checks allowedSwapper[pool][router] = true → passes
// userB swaps successfully despite not being allowlisted ✗
```

The existing unit tests in `SwapAllowlistSubExtension.t.sol` only test direct extension calls with `vm.prank(address(pool))` and do not cover the router-mediated path, which is why this bypass is not caught by the current test suite. [7](#0-6)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L38-40)
```text
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
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
