Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address as Swapper, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded by the pool, which is the `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router's address, not the actual user. If the pool admin allowlists the router (the only way to permit any router-mediated swap), every non-allowlisted address can bypass the gate by calling the router. There is no configuration that simultaneously permits allowlisted users to use the router and blocks non-allowlisted users.

## Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- whoever called pool.swap()
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension via `_callExtensionsInOrder`:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call. The actual end-user identity is stored only in transient callback context and is never forwarded to the pool or any extension:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn); // real user stored here only
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ...); // router is msg.sender here
```

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. The extension therefore sees `sender = router address` for every router-mediated swap, regardless of who initiated it. This creates an irresolvable dilemma: if the router is not allowlisted, no user can swap via the router even if individually allowlisted; if the router is allowlisted, every user bypasses the gate.

## Impact Explanation

Any user blocked by `SwapAllowlistExtension` can bypass it by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutputSingle` / `exactOutput`). Pools configured to restrict swap access — for example, to prevent toxic order flow and protect LP principal — will accept swaps from any address once the router is allowlisted. This directly exposes LP funds to adverse selection and value leakage, constituting a broken core pool access-control mechanism with direct loss potential for LP principal.

## Likelihood Explanation

The router is the standard, documented entry point for end-users. A pool admin who wants allowlisted users to be able to use the router must allowlist the router address — this is the natural and expected configuration. Once the router is allowlisted, the bypass is reachable by any unprivileged user with no special setup, making this highly likely to be triggered in practice.

## Recommendation

The `sender` forwarded to extensions must reflect the ultimate economic actor, not the intermediate contract. Two concrete fixes:

1. **Router-side**: Have the router encode the real user address into `extensionData` and have `SwapAllowlistExtension` decode and check it when present, with a guard ensuring the override can only be set by a trusted router (e.g., verified via factory registry).
2. **Extension-side**: Add a dedicated `senderOverride` field to the extension interface so the router can supply the real user identity in a structured, verifiable way, with the pool enforcing that only registered routers may set it.

Either approach must ensure the override cannot be spoofed by a non-router caller.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is the only allowed user
  allowedSwapper[pool][router] = true         // admin allowlists router so alice can use it

Attack:
  bob (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({
        pool: pool,
        zeroForOne: true,
        amountIn: X,
        recipient: bob,
        ...
    })

  Execution path:
    router.exactInputSingle()
      → _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, bob, tokenIn)  // bob stored transiently, never forwarded
      → pool.swap(recipient=bob, ...) [msg.sender = router]
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓  (no revert)
        → swap executes, bob receives tokens

Result:
  bob swaps successfully despite not being on the allowlist.
  The allowlist guard is fully bypassed for any user routing through the router.
```

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
