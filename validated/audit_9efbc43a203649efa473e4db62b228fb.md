Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks `sender` (direct pool caller / router address) instead of the actual user, enabling per-user allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension` is documented as gating swaps by swapper address per pool, but `beforeSwap` checks the `sender` parameter, which is `msg.sender` of the `pool.swap()` call — the router address when users route through `MetricOmmSimpleRouter`. This creates an irreconcilable dual-state: the allowlist is configured for individual users, but the guard enforces it against the router contract. A pool admin cannot simultaneously enable router-based swaps and enforce per-user gating.

## Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist: [1](#0-0) 

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards this `sender` value directly to the extension: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly, making the router `msg.sender` of `pool.swap()`: [4](#0-3) 

Therefore `sender` passed to `beforeSwap` equals the router address, and the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly ignores `sender` and checks `owner` (the beneficial owner of the position): [5](#0-4) 

The asymmetry is the root cause: deposits gate by beneficial owner; swaps gate by the direct caller (the router, not the user).

## Impact Explanation

The pool admin faces a forced binary choice with no correct option:

1. **Allowlist the router**: `allowedSwapper[pool][router] = true` passes the check for every user who calls the router, regardless of whether that user is individually allowlisted. Any unprivileged user can swap in a pool intended to be private or KYC-gated — the per-user allowlist is completely bypassed.

2. **Do not allowlist the router**: All router-based swaps revert with `NotAllowedToSwap()` even for individually allowlisted users, making the primary user-facing entry point unusable for the pool.

This is an admin-boundary break: an unprivileged path (the router) bypasses the access control the pool admin configured. For a private or compliance-gated pool, case (1) allows unauthorized users to interact with a pool that may carry compliance obligations or favorable pricing terms.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Any pool that deploys `SwapAllowlistExtension` expecting users to interact via the router will encounter this issue immediately. No special permissions are required — any user can call the router. The trigger is the normal, intended usage pattern.

## Recommendation

The extension should check the address that is the actual beneficiary of the swap. The `recipient` parameter (second argument to `beforeSwap`) is the address receiving output tokens and is set by the user even when routing through the router. A minimal fix mirroring `DepositAllowlistExtension`:

```solidity
function beforeSwap(address, address recipient, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Alternatively, the router could encode the originating user address in `extensionData` and the extension could decode and check it, providing stronger guarantees if `recipient` can be set to an arbitrary address.

## Proof of Concept

```solidity
// Pool configured with SwapAllowlistExtension; only `allowedUser` is allowlisted.
// allowedSwapper[pool][allowedUser] = true
// allowedSwapper[pool][router]      = false (or true — both cases break the invariant)

// Case 1: router NOT allowlisted — allowedUser cannot swap via router
vm.prank(allowedUser);
router.exactInputSingle(...); // reverts NotAllowedToSwap — sender=router is not allowlisted

// Case 2: router IS allowlisted — any user bypasses the allowlist
swapAllowlist.setAllowedToSwap(pool, address(router), true);
vm.prank(unauthorizedUser);
router.exactInputSingle(...); // succeeds — sender=router is allowlisted, user check skipped
assertEq(allowedSwapper[pool][unauthorizedUser], false); // user was never allowlisted
```

The existing test `test_allowedSwapSucceeds` in `FullMetricExtensionTest` confirms the extension works when the direct caller (`callers[0]`, a `TestCaller` contract) is allowlisted — but this test bypasses the router entirely, masking the router-mediated bypass path. [6](#0-5)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
