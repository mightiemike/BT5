### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Enabling Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap` call. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router, not the actual user. If the pool admin allowlists the router (a natural step to let allowlisted users use the router), every user — including those not individually allowlisted — can bypass the per-user gate by routing through the router.

### Finding Description

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to the extension dispatcher: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value just described — the router address when the call originates from `MetricOmmSimpleRouter`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly with no forwarding of the original user identity: [4](#0-3) 

**Contrast with `DepositAllowlistExtension`**, which correctly checks `owner` (the second argument — the actual LP owner explicitly supplied by the liquidity adder), not `sender` (the caller of `pool.addLiquidity`): [5](#0-4) 

The asymmetry is the root cause: the deposit gate keys on the economically relevant actor (`owner`); the swap gate keys on the transport-layer caller (`sender` = router).

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` to restrict trading to specific addresses faces an inescapable dilemma:

- **Router not allowlisted**: individually allowlisted users cannot use `MetricOmmSimpleRouter` at all — broken core swap functionality for legitimate users.
- **Router allowlisted** (the natural fix to restore router access for allowlisted users): `allowedSwapper[pool][router]` becomes `true`, so the guard passes for every caller of the router, including addresses the admin never intended to permit. Any unprivileged user bypasses the per-user allowlist by routing through the router.

The second scenario is an admin-boundary break: the pool admin's access-control policy is circumvented by an unprivileged path (calling the public router). On a pool designed to restrict trading to vetted counterparties, unauthorized swaps can drain LP assets at oracle-quoted prices.

### Likelihood Explanation

The trigger is a two-step sequence that any user can execute without special privileges:

1. Pool admin allowlists the router (a reasonable, non-malicious action to let their vetted users use the standard periphery).
2. Any non-allowlisted user calls `router.exactInputSingle` or `router.exactInput` targeting the curated pool.

No privileged knowledge or malicious setup is required beyond the pool existing with the extension configured.

### Recommendation

The `SwapAllowlistExtension` must gate the economically relevant actor, not the transport-layer caller. Two viable approaches:

1. **Mirror the deposit pattern**: have the router encode the originating user address in `extensionData` and have the extension decode and check it. This requires a convention between the router and the extension.
2. **Explicit recipient check**: gate on `recipient` (the second argument to `beforeSwap`) rather than `sender`, since the recipient is the address that receives swap output and is harder to spoof without cooperation.
3. **Document and enforce direct-call-only**: if the extension is intentionally incompatible with the router, revert when `sender` is a known router address and document that curated pools must be accessed directly.

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Admin calls setAllowedToSwap(pool, userA, true)   // allowlist userA
3. Admin calls setAllowedToSwap(pool, router, true)  // allowlist router so userA can use it
4. UserB (not allowlisted) calls:
       router.exactInputSingle({pool: pool, tokenIn: ..., ...})
5. Router calls pool.swap(recipient, ...) with msg.sender = router.
6. Pool calls extension.beforeSwap(router /*sender*/, ...)
7. Extension evaluates: allowedSwapper[pool][router] == true  → passes
8. UserB's swap executes on the curated pool, bypassing the per-user allowlist.
```

The check that should have fired — `allowedSwapper[pool][userB]` — is never evaluated because `sender` is the router, not `userB`.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
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
