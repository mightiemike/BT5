Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against `allowedSwapper[pool][sender]`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so the extension checks `allowedSwapper[pool][router]` — a single boolean shared by all callers — rather than the identity of the actual trader. Any pool admin who allowlists the router to enable legitimate router-mediated swaps inadvertently grants every unprivileged user the ability to bypass the allowlist entirely.

## Finding Description
In `MetricOmmPool.swap`, the pool invokes `_beforeSwap` with its own `msg.sender` as the `sender` argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value unchanged as the first argument to every configured extension. `SwapAllowlistExtension.beforeSwap` then evaluates: [2](#0-1) 

Here `msg.sender` is the pool and `sender` is whatever address called the pool. When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls the pool, the pool's `msg.sender` is the router: [3](#0-2) 

The router passes `params.extensionData` directly to the pool without encoding the original `msg.sender` into it: [4](#0-3) 

The extension has no mechanism to recover the actual initiator. The check `allowedSwapper[pool][router]` is a single boolean that is either true for all callers or false for all callers. There are no existing guards in `SwapAllowlistExtension` that inspect `extensionData` or verify the identity of the economic actor behind the router call.

## Impact Explanation
A pool admin who wants allowlisted users to use the standard periphery router must call `setAllowedToSwap(pool, router, true)`. Once set, `allowedSwapper[pool][router]` returns `true` for every call arriving through the router, regardless of who initiated it. Any unprivileged address can then execute swaps on a pool intended to be restricted to a curated set of counterparties. This breaks the core curation invariant enforced by `SwapAllowlistExtension` and exposes LP funds to unauthorized traders — a broken core pool functionality / admin-boundary bypass by an unprivileged path.

## Likelihood Explanation
The trigger requires the pool admin to have allowlisted the router. This is a natural and expected operational step: without it, allowlisted users cannot use the standard periphery router at all. Any pool that intends to support router-mediated swaps for its curated users will reach this configuration. The attacker requires no special role — a single call to `exactInputSingle` through the deployed router is sufficient, and the attack is repeatable indefinitely.

## Recommendation
The extension must gate on the economically relevant actor, not the immediate caller of the pool. Two sound approaches:

1. **Pass the original initiator through the router.** Have `MetricOmmSimpleRouter` encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check that value. The extension must also verify that `msg.sender` (the pool's caller) is a trusted router before trusting the encoded identity.

2. **Check `sender` and fall back to `extensionData`.** When `sender` is a known router, decode the real initiator from `extensionData` and check that address against the allowlist instead.

Either approach must be applied consistently across all router entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`).

## Proof of Concept
```solidity
// Setup: pool admin allowlists the router so legitimate users can swap via router
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Attacker (not in the allowlist) calls through the router
IMetricOmmSimpleRouter.ExactInputSingleParams memory params = IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    tokenIn: address(token0),
    recipient: attacker,
    deadline: block.timestamp + 1,
    amountIn: 1000,
    amountOutMinimum: 0,
    zeroForOne: true,
    priceLimitX64: 0,
    extensionData: ""
});

// Succeeds: extension checks allowedSwapper[pool][router] == true
// attacker is not in allowedSwapper[pool][attacker], but that check never runs
router.exactInputSingle(params);
```

The check at `SwapAllowlistExtension.sol` line 37 evaluates `allowedSwapper[pool][router]` — `true` — and the swap proceeds for the unauthorized attacker. [5](#0-4)

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
