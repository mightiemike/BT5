Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Allowlist Bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. This creates two failure modes: (A) if the router is allowlisted to support periphery swaps, every unprivileged address can bypass the allowlist by calling through the router; (B) if the router is not allowlisted, individually allowlisted users cannot use `MetricOmmSimpleRouter` at all.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,  // <-- direct caller, not original user
  recipient, ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension via `_callExtensionsInOrder`. `SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

Here `msg.sender` is the pool and `sender` is the forwarded value. When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router directly calls `pool.swap(...)`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

The router passes `params.extensionData` (user-supplied, no enforced encoding of original caller) and is itself the `msg.sender` to the pool. The extension receives `sender = router address`, so the lookup becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. No existing guard in the extension, pool, or router corrects this identity mismatch.

**Mode A (bypass):** Pool admin allowlists the router to enable periphery swaps → `allowedSwapper[pool][router] = true` → every caller through the router passes the check regardless of individual allowlist status.

**Mode B (DoS):** Router is not allowlisted → individually allowlisted users' swaps revert with `NotAllowedToSwap` when using the standard periphery path.

`DepositAllowlistExtension` does not share this flaw because it checks `owner` (the position owner argument), which is passed correctly regardless of who `sender` is.

## Impact Explanation
Mode A is the higher-severity path. A curated pool with `SwapAllowlistExtension` is designed to restrict trading to a known set of counterparties (e.g., KYC'd addresses, protocol-owned accounts, or whitelisted market makers). Once the router is allowlisted to support the standard periphery flow, the curation is entirely defeated: any address can trade on the pool at oracle-quoted prices, causing direct loss of LP principal through unrestricted access to a pool intended to be gated. This matches the "allowlist bypass" and "broken core pool functionality causing loss of funds" impact categories. Mode B causes the standard swap interface to be unusable for legitimate users on allowlisted pools.

## Likelihood Explanation
The trigger is unprivileged: any user can call `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The precondition for Mode A is that the pool admin has allowlisted the router — a natural and necessary operational step for any pool that intends to support the standard periphery. The precondition for Mode B requires only that the pool has `SwapAllowlistExtension` active and the router is not individually allowlisted, which is the default state. Both modes are reachable through normal, documented usage of the protocol's own periphery contracts.

## Recommendation
The extension should check the original end-user identity, not the immediate pool caller. The simplest safe fix is to have `MetricOmmSimpleRouter` encode the original `msg.sender` in a standardized prefix of `extensionData`, and have `SwapAllowlistExtension.beforeSwap` decode and gate on that address when the immediate `sender` is a known, factory-registered router. Alternatively, pass the original user through the router via `extensionData` and have the extension decode and check it, requiring a protocol-level convention for the extension payload.

## Proof of Concept
```
Setup:
  1. Deploy pool with SwapAllowlistExtension configured in BEFORE_SWAP_ORDER.
  2. Pool admin calls setAllowedToSwap(pool, router, true)
     — necessary to allow any router-mediated swap.
  3. Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack (Mode A — bypass):
  4. attacker calls router.exactInputSingle({pool: pool, ...}).
  5. Router calls pool.swap(...) with msg.sender = router.
  6. Pool calls _beforeSwap(router, ...).
  7. SwapAllowlistExtension checks allowedSwapper[pool][router] → true.
  8. Swap executes. attacker receives output tokens.
  9. Allowlist invariant broken: attacker was never individually allowlisted.

Attack (Mode B — DoS):
  4. Pool admin calls setAllowedToSwap(pool, alice, true).
  5. alice calls router.exactInputSingle({pool: pool, ...}).
  6. Router calls pool.swap(...) with msg.sender = router.
  7. SwapAllowlistExtension checks allowedSwapper[pool][router] → false.
  8. Revert: NotAllowedToSwap.
  9. alice cannot use the standard periphery despite being individually allowlisted.
```