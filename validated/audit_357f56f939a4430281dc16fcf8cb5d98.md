Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Allowlist Bypass - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to `SwapAllowlistExtension.beforeSwap`. When `MetricOmmSimpleRouter` intermediates a swap, `sender` becomes the router's address rather than the originating user. This forces the pool admin into an impossible choice: either the router is not allowlisted (breaking router-mediated swaps for all users) or the router is allowlisted (allowing every non-allowlisted user to bypass the restriction by routing through the router).

## Finding Description
**Root cause:** `MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`:

`metric-core/contracts/MetricOmmPool.sol` L230–240:
```solidity
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap()
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` encodes that `sender` verbatim into the extension call (`metric-core/contracts/ExtensionCalling.sol` L160–176).

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()` (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol` L37).

**Exploit path:** `MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `IMetricOmmPoolActions(params.pool).swap(...)` directly (`metric-periphery/contracts/MetricOmmSimpleRouter.sol` L72–80, L104–112, L136–137, L165–181). This makes the router `msg.sender` of `pool.swap()`, so `sender` passed to the extension is the router address, not the originating user.

**Why existing guards fail:** The allowlist check `allowedSwapper[pool][sender]` is structurally correct only for direct pool callers. There is no mechanism in the call chain to propagate the original economic actor through the router → pool → extension path. The `extensionData` field is user-supplied and unverified, so it cannot be trusted to carry the real user identity without additional authentication.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to KYC'd or institutional users is fully bypassed. Any unprivileged user can trade on the curated pool by calling `MetricOmmSimpleRouter` instead of the pool directly. Liquidity providers who deposited under the assumption that only vetted counterparties could trade against them are exposed to unauthorized value extraction. This constitutes a broken core pool functionality (allowlist invariant) causing potential loss of funds to LPs, meeting the "Broken core pool functionality causing loss of funds" impact gate.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the standard documented periphery entry point for swaps. No privileged access, special tokens, or unusual conditions are required. Any user aware that a pool uses `SwapAllowlistExtension` can trivially bypass it. The bypass is repeatable and unconditional as long as the router is allowlisted (which is required for any router-mediated swap to succeed).

## Recommendation
Pass the original economic actor through the call chain rather than the immediate `msg.sender`. Two concrete options:

1. **Extension-data approach:** Require the router to encode the originating user in `extensionData`, and have `SwapAllowlistExtension` decode and check that address, with the pool verifying the router is a trusted periphery contract before accepting the override.
2. **Sender-override approach:** Add an authenticated `swapOnBehalf(address realSender, ...)` entry point on the pool that trusted periphery contracts can call, passing the real user as `sender` to extensions.

## Proof of Concept
```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is KYC'd
3. Pool admin calls setAllowedToSwap(pool, router, true)  // required for router to work
4. Bob (not KYC'd) calls router.exactInputSingle({pool, ...})
   → router calls pool.swap(recipient, ...)               // msg.sender = router
   → pool calls _beforeSwap(sender=router, ...)
   → extension checks allowedSwapper[pool][router] == true → PASSES
5. Bob successfully swaps on the curated pool, bypassing the allowlist.
```

Foundry test outline:
- Deploy pool with `SwapAllowlistExtension` configured.
- `setAllowedToSwap(pool, alice, true)` and `setAllowedToSwap(pool, router, true)`.
- `vm.prank(bob)` → `router.exactInputSingle(...)` → assert swap succeeds despite Bob not being allowlisted.
- Confirm `vm.prank(bob)` → `pool.swap(...)` directly → assert revert with `NotAllowedToSwap`.