Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the Originating Swapper, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`MetricOmmPool.swap` passes `msg.sender` verbatim as the `sender` argument to every `beforeSwap` hook. When a user routes through the public, permissionless `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the originating EOA. `SwapAllowlistExtension.beforeSwap` keys its allowlist check on that `sender` value, meaning it checks whether the **router** is allowlisted rather than whether the **user** is allowlisted. Any non-allowlisted user can therefore bypass a curated pool's swap gate by routing through the router, or every allowlisted user who uses the router is DoS'd if the router is not allowlisted.

## Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to every hook.**

`MetricOmmPool.swap` captures `msg.sender` and forwards it verbatim to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- always the direct caller of pool.swap
  recipient,
  ...
);
```

When the call originates from `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`), `msg.sender` at the pool is the router contract address.

**Step 2 — `ExtensionCalling._beforeSwap` relays that value unchanged to every configured extension.**

`ExtensionCalling._beforeSwap` at lines 149–177 encodes `sender` directly into the `abi.encodeCall` payload and dispatches it to each extension in order. No transformation or substitution occurs.

**Step 3 — `SwapAllowlistExtension.beforeSwap` keys its allowlist lookup on `sender`.**

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension's caller) and `sender` is the value forwarded from `MetricOmmPool.swap` — i.e., the router address when the user routes through `MetricOmmSimpleRouter`.

**Step 4 — Exploit path.**

`MetricOmmSimpleRouter` is a public, permissionless contract. Any EOA can call it. When it calls `pool.swap(...)`, the pool sees `msg.sender = router`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][Bob]`.

| Scenario | What the extension checks | Outcome |
|---|---|---|
| Router is allowlisted | Router address ✓ | Every non-allowlisted user bypasses the gate via the router |
| Router is not allowlisted | Router address ✗ | Every allowlisted user who uses the router is DoS'd |

**Existing guards are insufficient.** There is no mechanism in the pool, the extension, or the router to recover the originating EOA from the call context. The `extensionData` field is caller-controlled and not authenticated, so it cannot be used as-is to attest the real caller.

## Impact Explanation

**Bypass path (router allowlisted or `allowAllSwappers = true`):** A non-allowlisted user routes through `MetricOmmSimpleRouter`. The extension sees the router's address, which passes the allowlist check. The user executes swaps on a pool that was designed to be curated. LP funds in the pool are exposed to actors the pool admin explicitly intended to exclude — direct loss of LP principal or fee revenue above Sherlock thresholds. This is an admin-boundary break: the pool admin's configured access control policy is silently nullified by an unprivileged path.

**DoS path (router not allowlisted):** Legitimate allowlisted users who interact through the supported periphery router are blocked. Core swap functionality is broken for the standard user-facing entry point.

Both paths are fund-impacting. The bypass path is the higher-severity outcome and maps directly to the "Admin-boundary break" and "Broken core pool functionality causing loss of funds" allowed impacts.

## Likelihood Explanation

- `MetricOmmSimpleRouter` is a public, permissionless contract — any EOA can call it with no special privilege, token, or setup beyond knowing the pool address.
- The bypass is deterministic and repeatable on every block.
- Pool admins have no on-chain mechanism to distinguish "router called by allowlisted user" from "router called by non-allowlisted user" under the current design.
- The `allowedSwapper` mapping is keyed by `(pool, sender)` where `sender` is always the direct caller of `pool.swap`, making the mismatch structural and unavoidable without a code change.

Likelihood: **High**.

## Recommendation

The allowlist must gate the economically relevant actor — the originating user — not the intermediary router. The cleanest fix is to have the router encode `msg.sender` into `extensionData`, and have `SwapAllowlistExtension` decode and check that value when `sender` is a known, trusted router address. Alternatively, require direct pool interaction for allowlisted pools and add a factory-level flag to enforce this. Checking `recipient` instead of `sender` is only a partial fix since `recipient` can also be set arbitrarily.

## Proof of Concept

```
1. Pool admin deploys a pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, Alice, true).
   Bob (0xBob) is NOT added to the allowlist.
3. Pool admin also calls setAllowedToSwap(pool, router, true)
   (treating the router as a trusted intermediary).
4. Bob calls MetricOmmSimpleRouter.exactInputSingle(..., pool, ...).
5. Router calls pool.swap(recipient=Bob, ...) — pool sees msg.sender = router.
6. Pool calls _beforeSwap(sender=router, ...).
7. SwapAllowlistExtension.beforeSwap checks allowedSwapper[pool][router] → true.
8. Bob's swap executes on the curated pool despite not being allowlisted.
9. Bob repeats this for every block, executing trades the pool admin intended to restrict.

Alternatively, if the router is NOT allowlisted:
4. Alice calls MetricOmmSimpleRouter.exactInputSingle(..., pool, ...).
5. Router calls pool.swap(...) — pool sees msg.sender = router.
6. SwapAllowlistExtension.beforeSwap checks allowedSwapper[pool][router] → false.
7. Alice's swap reverts with NotAllowedToSwap() despite being explicitly allowlisted.
```

Foundry test plan: deploy a pool with `SwapAllowlistExtension`, allowlist Alice but not Bob, allowlist the router address, then call `router.exactInputSingle` as Bob and assert the swap succeeds (bypass confirmed). In a second test, remove the router from the allowlist and assert Alice's router swap reverts (DoS confirmed).