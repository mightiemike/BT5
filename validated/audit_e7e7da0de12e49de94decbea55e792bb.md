The code confirms all three key facts in the claim:

1. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the immediate pool caller. [1](#0-0) 

2. `MetricOmmPool.swap` passes `msg.sender` (the immediate caller) as `sender` to `_beforeSwap`. [2](#0-1) 

3. `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly without encoding the originating user, making the router the pool's `msg.sender`. [3](#0-2) 

---

Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the Economic Swapper, Allowing Any User to Bypass the Curated-Pool Allowlist — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which resolves to `msg.sender` of `pool.swap` — the immediate caller, not the economic actor. When `MetricOmmSimpleRouter` is used, the pool sees the router as `sender`. Because the router must be allowlisted for any allowlisted user to access the pool via standard periphery, allowlisting the router silently grants every public user unrestricted swap access, rendering the curated-pool gate completely ineffective.

## Finding Description
`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`, passing the immediate caller as `sender` (L231). `ExtensionCalling._beforeSwap` forwards this verbatim to the extension via `abi.encodeCall`. `SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `IMetricOmmPoolActions(params.pool).swap(...)` directly (L72–80), so the pool's `msg.sender` is the router contract. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. For any allowlisted user to use the router at all, the pool admin must add the router to the allowlist. Once the router is allowlisted, every caller of any public router entry point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) passes the check regardless of their own allowlist status. No existing guard in the extension, pool, or router prevents this substitution.

## Impact Explanation
A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to a specific set of counterparties. The moment the router is added to the allowlist — required for any allowlisted user to use the standard periphery — the allowlist becomes entirely ineffective: any address can call the public router and execute swaps on the restricted pool. This is an admin-boundary break where an unprivileged path (the public router) circumvents a factory-registered extension guard, silently invalidating the pool admin's configured security boundary without any privileged action by the attacker.

## Likelihood Explanation
The router is the standard, documented periphery entry point. Any pool that wants to support normal user tooling must allowlist it. The bypass requires only a standard router call — no special permissions, no flash loans, no multi-step setup. Any user who knows the pool is allowlist-gated can trivially route through the public router. The condition is met automatically the moment the admin enables router access for legitimate users.

## Recommendation
The extension must gate the economic actor, not the immediate pool caller. The simplest correct fix is to have the router encode `msg.sender` (the originating user) into `extensionData`, and have `SwapAllowlistExtension.beforeSwap` decode and verify that identity instead of (or in addition to) `sender`. Alternatively, maintain a registry of trusted forwarder contracts in the extension; when `sender` is a known forwarder, require the real user identity to be present and verified in `extensionData`.

## Proof of Concept
```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension in BEFORE_SWAP_ORDER
  admin calls swapExt.setAllowedToSwap(pool, alice, true)
  admin calls swapExt.setAllowedToSwap(pool, router, true)   // required for alice to use router
  bob = any non-allowlisted EOA

Attack:
  bob calls router.exactInputSingle({pool: pool, recipient: bob, ...})
  → router calls pool.swap(bob, ...)                          // router is msg.sender
  → pool calls _beforeSwap(msg.sender=router, ...)
  → SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  → swap executes for bob despite bob not being on the allowlist

Result:
  bob successfully swaps on a pool that should have blocked him.
  The allowlist is completely ineffective for any user who routes through the public router.
```

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L230-232)
```text
    _beforeSwap(
      msg.sender,
      recipient,
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
