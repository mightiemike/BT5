Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the originating user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against `allowedSwapper[pool][sender]`. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the router's address because `MetricOmmPool.swap` passes `msg.sender` (the router) as `sender` to the extension. Any pool admin who allowlists the router to support router-mediated swaps simultaneously grants every user on the network the ability to bypass the per-user gate.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
);
```

`SwapAllowlistExtension.beforeSwap` checks this `sender` against the allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` of that call:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

Therefore the hook evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. A pool admin who adds the router to the allowlist (the only way to permit router-mediated swaps on a curated pool) simultaneously grants every user the ability to bypass the per-user gate.

The asymmetry with `DepositAllowlistExtension` confirms the design intent: that hook correctly ignores `sender` and checks `owner` (the LP position owner explicitly supplied by the caller), so the deposit gate is not affected by router intermediation. Only the swap gate is broken.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` and `allowAllSwappers = false` is intended to restrict trading to a curated set of counterparties (e.g., a private market-making pool, a KYC-gated venue, or a pool that excludes MEV bots). Once the router is allowlisted, any unpermissioned user can call `router.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` targeting the restricted pool and the hook passes. The unauthorized trader executes swaps at oracle-derived prices, draining LP-owned token reserves through arbitrage or directional flow that the allowlist was specifically designed to prevent. The loss is direct and irrecoverable: LP principal leaves the pool in exchange for the input token at the oracle mid, with no recourse. This matches the **Allowlist path** impact gate: deposit/swap allowlist checks must cover the exact actor/action intended and cannot be bypassed through router.

## Likelihood Explanation

The trigger condition is that the pool admin allowlists the router. This is the natural and necessary operational step any pool admin must take if they want to support router-mediated swaps for their allowlisted users — there is no other mechanism to do so. The admin cannot selectively allow specific users through the router; the only granularity available is the router address itself. The bypass is therefore reachable on any curated pool that supports the standard periphery router, which is the expected production configuration. No special attacker capability is required beyond calling the public router entry-points.

## Recommendation

The hook must verify the originating user, not the immediate caller. The cleanest fix is extension-data attestation: require the router to encode the originating `msg.sender` into `extensionData` for each hop, and have `SwapAllowlistExtension` decode and verify it. The router already threads per-hop `extensionData` through to the pool via `params.extensionData`. Concretely:

1. In each router entry-point, prepend `abi.encode(msg.sender)` to the swap's `extensionData`.
2. In `SwapAllowlistExtension.beforeSwap`, decode the first 32 bytes of `extensionData` as the originating user and check `allowedSwapper[pool][originatingUser]` instead of `allowedSwapper[pool][sender]`.

This preserves the router's role as a trusted intermediary while restoring per-user gating.

## Proof of Concept

```
Setup:
  pool P has SwapAllowlistExtension E with allowAllSwappers[P] = false
  pool admin calls E.setAllowedToSwap(P, router, true)   // to enable router swaps
  pool admin calls E.setAllowedToSwap(P, alice, true)    // intended: only alice may swap

Attack (by bob, not allowlisted):
  bob calls router.exactInputSingle({
      pool: P,
      zeroForOne: true,
      amountIn: X,
      recipient: bob,
      ...
  })
  → router calls P.swap(bob, true, X, ...)   // msg.sender = router
  → pool calls _beforeSwap(router, bob, ...)
  → pool calls E.beforeSwap(router, bob, ...)
  → hook checks allowedSwapper[P][router] == true  ✓  (passes)
  → bob's swap executes; LP funds transferred to bob

Result: bob, who was never allowlisted, swaps successfully in a pool
        that was supposed to be restricted to alice only.
```

Foundry test plan: deploy a pool with `SwapAllowlistExtension`, set `allowedSwapper[pool][router] = true` and `allowedSwapper[pool][alice] = true` only, then call `router.exactInputSingle` as `bob` and assert the swap succeeds (no revert), confirming the bypass. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
