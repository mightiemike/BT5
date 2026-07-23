Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the originating user, allowing any caller to bypass a pool's swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool, which is always `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. Any pool admin who allowlists the router (required for allowlisted users to use it) simultaneously grants every non-allowlisted user the ability to bypass the gate by calling the same public router.

## Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

`MetricOmmPool.swap` calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

When the router calls `pool.swap()`, `msg.sender` inside the pool is the router contract address, so `sender = router`.

**Step 2 — Extension checks `sender` (the router) against the allowlist.**

`SwapAllowlistExtension.beforeSwap` uses `msg.sender` (the pool) as the mapping key and `sender` (the router) as the identity being gated: [2](#0-1) 

The allowlist is keyed `pool → swapper → bool`: [3](#0-2) 

**Step 3 — Router calls the pool on behalf of the user.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, so the pool sees `msg.sender = router`, not the original caller: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

**Why existing guards fail:**

The `allowAllSwappers` short-circuit and the `allowedSwapper` check both operate on `sender`, which is the router. There is no mechanism in the extension to recover the original `msg.sender` of the router call. The dilemma is irresolvable under the current design:

| Admin choice | Effect |
|---|---|
| Allowlist the router | Every non-allowlisted user bypasses the gate via the router |
| Do not allowlist the router | Allowlisted users cannot use the router at all |

## Impact Explanation

Any unprivileged user can execute swaps on a curated pool that is configured to restrict swaps to specific addresses (e.g., KYC'd counterparties). The allowlist provides zero protection once the router is allowlisted. LP positions priced under the assumption of a restricted, trusted counterparty set are exposed to unrestricted trading, constituting a direct loss of LP principal and a broken core pool invariant. This meets the "broken core pool functionality causing loss of funds" and "admin-boundary break bypassed by an unprivileged path" criteria.

## Likelihood Explanation

The router is the primary user-facing swap interface deployed alongside the pool. Any pool admin who wants allowlisted users to use the router must allowlist the router address — this is the natural, expected configuration. The bypass requires no special preconditions beyond calling the public router, is repeatable indefinitely, and is reachable on every production curated pool that supports router-mediated swaps.

## Recommendation

The extension must check the **original user**, not the intermediary. Two sound approaches:

1. **Pass the original user through the router.** Have the router forward the original `msg.sender` as an explicit `swapper` field inside `extensionData`. The extension decodes and checks that field instead of the `sender` argument.

2. **Trusted-router registry in the extension.** When `sender` is a known router, decode the real swapper from `extensionData`; otherwise check `sender` directly. This preserves direct-call behavior while correctly gating router-mediated calls.

The invariant that must hold: `allowedSwapper[pool][realUser]` is checked regardless of whether the user enters through the router or calls the pool directly.

## Proof of Concept

```
Setup:
  pool   = MetricOmmPool with SwapAllowlistExtension
  alice  = allowlisted KYC user
  bob    = non-allowlisted attacker
  router = MetricOmmSimpleRouter (allowlisted so alice can use it)

Admin actions:
  extension.setAllowedToSwap(pool, alice,  true)   // alice is allowed
  extension.setAllowedToSwap(pool, router, true)   // required for alice to use router

Attack:
  bob calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient=bob, ...)          // MetricOmmSimpleRouter.sol L72-80
    → pool calls _beforeSwap(sender=router, ...)          // MetricOmmPool.sol L230-240
    → extension checks allowedSwapper[pool][router]==true // SwapAllowlistExtension.sol L37
    → swap executes for bob despite bob not being allowlisted

Result:
  bob swaps on a curated pool that was supposed to block him.
  The allowlist provides zero protection once the router is allowlisted.
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
