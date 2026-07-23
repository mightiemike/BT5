Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the actual user, allowing any user to bypass a curated pool's per-user swap allowlist via the router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the actual user. A pool admin who allowlists the router — the only way to let allowlisted users use the router — inadvertently grants every address the ability to bypass the per-user gate by routing through the router.

## Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check as:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension's caller), and `sender` is the first argument forwarded by the pool. [1](#0-0) 

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards that value unchanged into the ABI-encoded call to the extension: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` inside the pool: [4](#0-3) 

So the extension receives `sender = router_address`, and the check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

There is no mechanism in the router to inject the actual caller identity into `extensionData` automatically; `params.extensionData` is passed through verbatim from the caller and is fully user-controlled.

## Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and wants allowlisted users to be able to use the router must allowlist the router contract itself. Once the router is allowlisted, `allowedSwapper[pool][router]` returns `true` for every call arriving through the router — regardless of who the actual user is. Any non-allowlisted address can call `router.exactInputSingle(pool=curatedPool, ...)` and the extension passes, bypassing the per-user gate entirely. The curated pool's swap restriction is rendered ineffective for all router-mediated paths. This constitutes broken core pool functionality: the access control mechanism the pool was configured to enforce is completely circumvented by an unprivileged actor using the standard supported periphery contract.

## Likelihood Explanation

The trigger is a normal, non-privileged user action (calling the public router). The only precondition is that the pool admin has allowlisted the router — a natural and expected configuration step for any admin who wants allowlisted users to access the pool through the standard periphery. There is no malicious setup assumption; the admin acts in good faith. The bypass is reachable on every pool that has both `SwapAllowlistExtension` configured and the router allowlisted.

## Recommendation

The extension should check the actual economic actor, not the immediate caller. The simplest fix consistent with the existing design is to gate on `recipient` (the second argument to `beforeSwap`), since the router always sets `recipient` to the actual user:

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

Alternatively, require the actual user identity to be passed via `extensionData` and verified against a signature or trusted forwarder pattern.

## Proof of Concept

```
Setup:
  pool = curated MetricOmmPool with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, alice, true)
  admin calls setAllowedToSwap(pool, router, true)  ← required for alice to use router

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  Flow:
    router → pool.swap(recipient=bob, ...)          [msg.sender inside pool = router]
    pool → _beforeSwap(sender=router, recipient=bob, ...)
    pool → extension.beforeSwap(sender=router, ...)
    extension checks: allowedSwapper[pool][router] == true  ✓
    swap executes for bob despite bob not being allowlisted

Result:
  bob trades on a pool that was supposed to restrict him.
  The per-user allowlist is completely bypassed for all router-mediated swaps.
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
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
```
