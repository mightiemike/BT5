Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the originating trader when swaps are routed through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of `pool.swap`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap`, so the extension checks the router's allowlist entry rather than the actual trader's. A pool admin who allowlists the router to enable router-mediated swaps inadvertently grants every caller of the router — including those never individually authorized — the ability to swap on the curated pool.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // whoever called pool.swap — the router, not the user
  recipient, ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-177
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks that forwarded `sender` against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput` all call `IMetricOmmPoolActions(pool).swap(...)` directly, making the router contract `msg.sender` of `pool.swap`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

When the pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps, `allowedSwapper[pool][router]` becomes `true`. From that point, `beforeSwap` evaluates `allowedSwapper[pool][router]` → `true` for every caller of the router, regardless of whether that caller is individually authorized. The per-user curation is silently voided.

## Impact Explanation
Any trader — including those explicitly excluded from the per-pool swap allowlist — can bypass the guard by routing through `MetricOmmSimpleRouter`. On a curated pool restricted to KYC'd counterparties or specific market makers, this allows unauthorized traders to execute swaps against the LP at oracle-derived bid/ask prices. The LP's principal is directly at risk because the pool settles trades at the configured price regardless of who the counterparty is. This constitutes a direct loss of user principal (LP assets) above Sherlock thresholds, qualifying as High severity.

## Likelihood Explanation
The trigger requires the pool admin to have allowlisted the router address. This is a natural and expected administrative action: the router is the protocol's own supported periphery contract, and an admin who wants users to be able to use it must allowlist it. The admin has no mechanism to distinguish "allow the router for my allowlisted users only" from "allow the router for everyone" because the extension only sees the router's address, not the originating user. Once the router is allowlisted, any unprivileged user can exploit the bypass with a single router call, repeatably and without special privileges.

## Recommendation
Pass the originating user's address through the router to the pool as a distinct `originator` field, and have the pool forward it to extensions instead of (or in addition to) `msg.sender`. Alternatively, `SwapAllowlistExtension.beforeSwap` should detect when `sender` is a known periphery contract and fall back to checking `tx.origin`, or the router should pass the user's address as an explicit parameter that the pool forwards to extensions. A short-term documentation fix — noting that allowlisting the router is equivalent to `allowAllSwappers = true` — is insufficient to prevent fund loss but should accompany any code fix.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
3. Pool admin never calls `setAllowedToSwap(pool, attacker, true)` (attacker is not individually authorized).
4. `attacker` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `IMetricOmmPoolActions(pool).swap(...)` — `msg.sender` of `pool.swap` is the router.
6. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender=router, ...)`.
7. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. `attacker` successfully swaps on a pool they were never individually authorized to access, receiving tokens at oracle-derived prices at the LP's expense. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
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
