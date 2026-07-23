Audit Report

## Title
SwapAllowlistExtension Gates on Router Address Instead of End User, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which resolves to `msg.sender` of the `pool.swap` call. When users route through `MetricOmmSimpleRouter`, `sender` becomes the router's address. Any pool admin who allowlists the router to support legitimate router-mediated swaps inadvertently grants every non-allowlisted user access to the curated pool, completely defeating the allowlist policy.

## Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check at line 37:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct for the pool-keyed mapping). `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`: [1](#0-0) 

`_beforeSwap` is called from `MetricOmmPool.swap` with `msg.sender` of the pool call as `sender`: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)`: [3](#0-2) 

At that point, `msg.sender` to the pool is the **router contract**, so `sender` forwarded to the extension is the router address — not the end user. The check `allowedSwapper[pool][router]` evaluates to `true` once the admin allowlists the router, and the revert is never reached for any caller of the router.

The same issue applies to `exactOutputSingle` and `exactOutput`: [4](#0-3) [5](#0-4) 

The allowlist check itself: [6](#0-5) 

## Impact Explanation

**Severity: High.** A pool configured with `SwapAllowlistExtension` (e.g., KYC-gated or institutional pool) can be fully bypassed by any user who calls `MetricOmmSimpleRouter`. The non-allowlisted user executes swaps at oracle-anchored prices against liquidity that was intended to be restricted to approved counterparties only. LP principal is at direct risk because the pool's curation invariant — that only approved counterparties trade against its liquidity — is broken. This is a direct loss-of-policy impact with fund consequences on every curated pool that supports router access.

## Likelihood Explanation

**Likelihood: High.** `MetricOmmSimpleRouter` is the standard, documented user-facing entry point for swaps. Any pool admin who deploys a curated pool and wants to support normal user tooling must allowlist the router. The bypass requires no special privileges, no flash loans, and no unusual token behavior — only a standard router call. The condition that triggers it (router allowlisted) is the expected production configuration for any curated pool that is not purely direct-call-only.

## Recommendation

The extension must resolve the actual end-user identity rather than the immediate `msg.sender` of the pool call. Two sound approaches:

1. **Check `recipient` instead of `sender`**: Gate on the `recipient` argument (the address that receives output tokens), which is the economically relevant actor the pool admin intends to gate. This is the second argument to `beforeSwap` (currently ignored with `address`).

2. **Require both `sender` and `recipient` to be allowlisted**: `allowedSwapper[pool][sender] && allowedSwapper[pool][recipient]`, so the router being allowlisted alone does not open the gate.

3. **Forward real caller via `extensionData`**: Have `MetricOmmSimpleRouter` encode the real user address in `extensionData`, and have `SwapAllowlistExtension` decode and check that address (requires a trusted forwarding convention).

The cleanest fix consistent with the codebase design is option 1 — key the allowlist on `recipient`, since that is the address that actually receives output tokens and is the economically relevant actor.

## Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — necessary so that allowlisted users can trade via the router.
3. Non-allowlisted attacker (address NOT in allowedSwapper[pool]) calls:
     MetricOmmSimpleRouter.exactInputSingle(pool, zeroForOne, amountIn, ...)
4. Router calls pool.swap(recipient=attacker, ...) [MetricOmmSimpleRouter.sol L72-80]
5. Pool calls _beforeSwap(sender=router, ...) [MetricOmmPool.sol L230-240]
6. Extension evaluates: allowedSwapper[pool][router] == true → passes [SwapAllowlistExtension.sol L37]
7. Swap executes. Attacker receives output tokens from a pool they were
   never authorized to trade against.
```

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-137)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L154-181)
```text
  function exactOutput(ExactOutputParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint8 tradesLeftAfterThis = uint8(params.pools.length - 1);
    address pool = params.pools[tradesLeftAfterThis];
    bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, tradesLeftAfterThis);
    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _initCallbackContextforRecursiveOutput(
      pool, CALLBACK_MODE_EXACT_OUTPUT_ITERATE, tradesLeftAfterThis, msg.sender, params.tokens[0]
    );
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
      .swap(
        params.recipient,
        zeroForOne,
        -expectedAmountOut,
        MetricOmmSwapPath.openLimit(zeroForOne),
        abi.encode(
          ExactOutputIterateCallbackData({
          tokens: params.tokens,
          pools: params.pools,
          extensionDatas: params.extensionDatas,
          zeroForOneBitMap: params.zeroForOneBitMap,
          amountInMax: params.amountInMaximum
        })
        ),
        params.extensionDatas[tradesLeftAfterThis]
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
