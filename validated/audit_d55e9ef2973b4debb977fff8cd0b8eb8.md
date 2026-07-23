Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of originating EOA, breaking allowlisted swaps and enabling allowlist bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is always `msg.sender` of the pool's `swap` call. When users interact through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating EOA. This produces two fund-impacting outcomes: allowlisted EOAs cannot use the router at all (broken core swap path), and if the admin allowlists the router to restore access, every non-allowlisted user can bypass the curated pool's swap gate.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)`, the pool's `msg.sender` is the router, not the originating EOA. The router stores the real user's address only in transient callback context (`_setNextCallbackContext`) for payment purposes — it is never forwarded to the pool as `sender` or encoded into `extensionData` for the extension to recover: [4](#0-3) 

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates by `owner` (the economic actor), not `sender` (the immediate caller), so the deposit path does not share this flaw: [5](#0-4) 

## Impact Explanation

**Scenario A — Broken core swap flow:** A pool configured with `SwapAllowlistExtension` and a curated allowlist of EOAs is immediately broken for all router-mediated swaps. An allowlisted EOA calling `exactInputSingle` causes the pool to see `sender = router`; since the router is not in `allowedSwapper`, the hook reverts with `NotAllowedToSwap`. The allowlisted user cannot use the protocol's primary swap periphery at all — broken core pool functionality.

**Scenario B — Allowlist bypass:** The natural admin remediation is to allowlist the router (`allowedSwapper[pool][router] = true`). Once done, any non-allowlisted EOA calling `router.exactInputSingle` targeting the curated pool will pass the check, because the pool sees `sender = router` which is now allowed. The entire swap allowlist is bypassed for every user routing through the router, defeating KYC-gated, institutional, or risk-limited pool policies.

Both outcomes meet the allowed impact gate: Scenario A is broken core pool functionality; Scenario B is an admin-boundary break reachable by an unprivileged trader.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the documented primary periphery for swaps. Any pool that configures `SwapAllowlistExtension` and expects users to interact through the router immediately hits Scenario A with no special setup. Scenario B follows as the natural and predictable admin remediation. Both paths require no special privileges, no malicious tokens, and no non-standard behavior — any public user can trigger them.

## Recommendation

Gate by the actual end-user, not the immediate pool caller. Two approaches:

1. **Preferred — pass real user via `extensionData`:** Modify the router to encode the originating `msg.sender` inside `extensionData` (with a trusted-router check or signature), and have `SwapAllowlistExtension` decode and verify it.

2. **Alternative — align with the deposit pattern:** Extend the pool's swap interface to carry an explicit `originator` field (analogous to `owner` in `addLiquidity`), and have the extension check that field instead of `sender`.

Until fixed, pools requiring a swap allowlist must not rely on `SwapAllowlistExtension` when the router is a supported entry-point.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   // alice is KYC-approved
  allowedSwapper[pool][bob]   = false  // bob is not approved

Scenario A — broken functionality (no admin action required):
  alice calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)   [msg.sender = router]
    → pool calls _beforeSwap(sender=router, ...)
    → extension checks allowedSwapper[pool][router] → false
    → revert NotAllowedToSwap
  alice (allowlisted) cannot use the router. ✗

Scenario B — bypass after natural admin remediation:
  admin calls swapExtension.setAllowedToSwap(pool, router, true)
    (to fix alice's problem above)

  bob calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)   [msg.sender = router]
    → pool calls _beforeSwap(sender=router, ...)
    → extension checks allowedSwapper[pool][router] → true
    → swap executes for bob (non-allowlisted user)
  bob bypasses the curated pool's swap gate. ✗
```

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
