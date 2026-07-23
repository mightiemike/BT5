Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Originating User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` receives the `sender` argument from `MetricOmmPool.swap`, which passes its own `msg.sender` — the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, that direct caller is the router contract, not the end user. A pool admin who allowlists the router to enable router-mediated swaps for curated users inadvertently opens the pool to every user on the router, completely defeating the allowlist. There is no configuration that correctly restricts router-mediated swaps to only the intended users.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // direct caller of pool.swap() — the router when routed
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that exact address against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly without forwarding the originating user's address:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
);
```

The pool always sees `msg.sender = router`. The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. There is no mechanism for the router to inject the originating EOA into the `sender` slot. A pool admin faces a structural impossibility: allowlisting individual users only means those users cannot use the router (router is not allowlisted → reverts); allowlisting the router means every user on the router bypasses the allowlist.

## Impact Explanation
When a pool admin allowlists `MetricOmmSimpleRouter` to support router-mediated swaps for curated counterparties, any unprivileged user can call `router.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) targeting that pool and the `beforeSwap` hook will pass — `allowedSwapper[pool][router] == true`. LPs in pools designed for trusted-only counterparties (e.g., RWA pools, institutional pools, or pools with asymmetric oracle pricing) are exposed to arbitrary swappers, which can lead to direct LP value loss if the pool's economics depend on counterparty selection.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the canonical periphery entry point for end users. A pool admin who deploys a curated pool and wants allowlisted users to benefit from multi-hop routing or slippage protection will naturally allowlist the router. The admin has no way to achieve "router-mediated swaps for specific users only" — the only path to enabling router access is the one that opens the pool to everyone. This makes the misconfiguration highly probable in practice.

## Recommendation
The `sender` forwarded to extension hooks must represent the economic actor, not the intermediary. Two complementary fixes:

1. **Router-side**: Have `MetricOmmSimpleRouter` encode the originating `msg.sender` in `extensionData` so extensions can recover the real user.
2. **Extension-side**: `SwapAllowlistExtension.beforeSwap` should decode and check the real user from `extensionData` when the direct `sender` is a known router, or the pool should expose a standardised "originator" field in the hook arguments.

Alternatively, document explicitly that allowlisting the router opens the pool to all router users, and provide a separate `RouterSwapAllowlistExtension` that reads the originator from a signed payload in `extensionData`.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is the intended curated user
  allowedSwapper[pool][router] = true         // admin adds router to support alice's router usage

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  Execution trace:
    router.exactInputSingle()
      → pool.swap(msg.sender=router, ...)          // MetricOmmPool.sol L231
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router)
            → allowedSwapper[pool][router] == true  ✓ passes  // SwapAllowlistExtension.sol L37
        → swap executes, bob receives output tokens

Result: bob, who is not on the allowlist, successfully swaps in a curated pool.
``` [1](#0-0) [2](#0-1) [3](#0-2)

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
