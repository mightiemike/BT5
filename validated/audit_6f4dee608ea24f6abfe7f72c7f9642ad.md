Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` at the pool level — the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract address, not the end user. Any pool admin who allowlists the router (required for router-mediated swaps to function) inadvertently grants every unpermissioned user the ability to bypass the curated allowlist by routing through the public periphery contract.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // direct caller of pool.swap(), not the end user
  recipient,
  ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that this `sender` (the direct pool caller) is allowlisted:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    ...
  );
```

At this point `msg.sender` inside `MetricOmmPool.swap()` is the router address, so the extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The router stores the real payer identity only in transient callback context (`_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn)`) which is never forwarded to extensions. No existing guard in `SwapAllowlistExtension`, `ExtensionCalling`, or `MetricOmmPool` recovers the original `msg.sender` from the router's transient storage.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC-verified addresses, institutional traders, or whitelisted market makers) is fully bypassed. Any unpermissioned address can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) and execute swaps on the restricted pool. This breaks the core curation invariant the extension is designed to enforce and exposes LP funds to adverse selection from actors the pool admin explicitly excluded — constituting a broken core pool functionality causing loss of funds.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary supported public swap entrypoint. Any pool using `SwapAllowlistExtension` that also wants to support router-based swaps for its permitted users must allowlist the router. Once that is done, the bypass is unconditional and requires no special privileges — any EOA or contract can exploit it by calling the public router functions. The attacker needs no tokens beyond the swap input amount and no elevated permissions.

## Recommendation
The `sender` argument forwarded to extensions should represent the economic actor (the end user), not the intermediary contract. Two complementary fixes:

1. **In the router**: embed the original `msg.sender` (the end user) in `extensionData` using a documented convention, so extensions can decode and verify the real swapper identity.
2. **In `SwapAllowlistExtension`**: decode the real user identity from `extensionData` when `sender` is a known router, or gate on the `recipient` argument (the address receiving output tokens) as a proxy for the economic actor.

Alternatively, document the pool as incompatible with the public router when `SwapAllowlistExtension` is active, and enforce this constraint at the factory or extension `initialize` level.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, trustedUser, true).
  - Pool admin calls setAllowedToSwap(pool, router, true) so trustedUser
    can swap via MetricOmmSimpleRouter.

Attack:
  - attacker (not in allowlist) calls:
      router.exactInputSingle(ExactInputSingleParams({
          pool: restrictedPool,
          recipient: attacker,
          ...
      }))
  - Router calls pool.swap(attacker, ...) → msg.sender at pool = router.
  - Pool calls _beforeSwap(sender=router, ...).
  - Extension checks allowedSwapper[pool][router] → true (router is allowlisted).
  - Swap executes. Attacker receives output tokens.

Result: attacker bypasses the curated allowlist and trades on a restricted pool.
```