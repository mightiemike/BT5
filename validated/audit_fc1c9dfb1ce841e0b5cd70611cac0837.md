Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the originating user, allowing any caller to bypass per-pool swap allowlists via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` always sets to `msg.sender` — the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract address, not the end user. If the pool admin allowlists the router (the only way to enable router-mediated swaps for any allowlisted user), every unpermissioned user can bypass the restriction by routing through the router, draining LP positions at oracle-derived prices.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol, line 230-240
_beforeSwap(
    msg.sender,   // router address when called via MetricOmmSimpleRouter
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` faithfully forwards this value to the extension without any secondary check:

```solidity
// metric-core/contracts/ExtensionCalling.sol, line 162-175
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, amountSpecified, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol, line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` to the pool:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol, line 72-80
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

There is no mechanism in the router to encode the originating user's address into `extensionData` or any other field. The extension has no way to recover the original EOA. A pool admin who wants to support router-mediated swaps for their allowlisted users must call `setAllowedToSwap(pool, routerAddress, true)` — there is no other path. Once the router is allowlisted, `allowedSwapper[pool][router]` is `true`, and the check passes for any caller who routes through the router.

## Impact Explanation
Any unpermissioned user can execute swaps on a curated pool that uses `SwapAllowlistExtension` by routing through `MetricOmmSimpleRouter`, as long as the pool admin has allowlisted the router address. Because the pool is oracle-anchored with no internal price discovery, every unauthorized swap extracts real value from LP positions at the current fair-market price. This constitutes a direct loss of LP principal and breaks the core invariant that a curated pool enforces the same allowlist policy regardless of which supported public entrypoint is used.

## Likelihood Explanation
Likelihood is high. `MetricOmmSimpleRouter` is the primary user-facing swap interface. A pool admin who wants to support router-mediated swaps for their allowlisted users must allowlist the router address — there is no alternative mechanism. Once the router is allowlisted, the bypass is immediately available to any public caller with zero additional preconditions: no privileged access, no special token, and no admin cooperation beyond the natural configuration step.

## Recommendation
The extension must gate the end user, not the intermediary contract. Two sound approaches:

1. **Pass the originating user through `extensionData`**: The router encodes the original `msg.sender` into `extensionData`; the extension decodes and verifies it. This is acceptable given the router is a protocol-controlled contract.
2. **Maintain a trusted-router registry in the extension**: When `sender` is a known router, extract the real user from `extensionData`; when `sender` is an EOA, check it directly.

The invariant must be: the address checked against the allowlist is the address that economically initiates the swap (the originating user), not the settlement intermediary.

## Proof of Concept
```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension (beforeSwap order = extension configured)
  admin calls setAllowedToSwap(pool, address(router), true)
    — required so that allowlisted users can swap via the router
  alice (allowlisted EOA) and bob (non-allowlisted EOA) both hold token1

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle(
      pool = pool,
      tokenIn = token1,
      zeroForOne = false,
      amountIn = X,
      recipient = bob,
      ...
  )

  Router calls pool.swap(recipient=bob, zeroForOne=false, ...)
  Pool calls _beforeSwap(sender=address(router), ...)
  Extension checks allowedSwapper[pool][router] → true → no revert
  Pool executes swap, sends token0 to bob, pulls token1 from router (which pulls from bob)

Result:
  bob successfully swaps on a pool he is not allowlisted for.
  The allowlist is completely bypassed.
  LP providers suffer a loss equal to the swap output at oracle price.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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
```
