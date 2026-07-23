Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Real User, Allowing Any Caller to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` sets to `msg.sender` — the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks whether the **router** is allowlisted rather than the actual end user. A pool admin who allowlists the router to support legitimate router-mediated swaps inadvertently opens the gate to every address on the internet, completely defeating the per-user access control the extension is designed to enforce.

## Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to `_beforeSwap`:**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)` at line 230:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- whoever called pool.swap()
  recipient,
  ...
  extensionData
);
```

**Step 2 — `ExtensionCalling._beforeSwap` forwards `sender` unchanged:**

`ExtensionCalling._beforeSwap` (L149-177) encodes and forwards the `sender` value verbatim to every configured extension via `_callExtensionsInOrder`.

**Step 3 — Extension checks the wrong address:**

`SwapAllowlistExtension.beforeSwap` (L37) checks:
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```
Here `msg.sender` is the pool (the extension's caller) and `sender` is whoever called `pool.swap()` — i.e., the router, not the end user.

**Step 4 — Router is `msg.sender` to the pool:**

`MetricOmmSimpleRouter.exactInputSingle` (L72-80) calls `pool.swap()` directly:
```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ...);
```
The router is `msg.sender` to the pool. The same applies to `exactInput` (L104), `exactOutputSingle` (L136), and `exactOutput` (L165).

**Exploit flow:**
1. Pool admin deploys pool with `SwapAllowlistExtension`; sets `allowedSwapper[pool][alice] = true`
2. Admin also sets `allowedSwapper[pool][router] = true` so alice can use the standard router
3. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, recipient: bob, ...})`
4. Router calls `pool.swap(bob, ...)` — router is `msg.sender` to pool
5. Pool calls `_beforeSwap(sender=router, ...)`
6. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds
7. Bob successfully swaps on the curated pool despite `allowedSwapper[pool][bob] == false`

**Existing guards are insufficient:** There is no mechanism in the pool, router, or extension to authenticate the real end-user identity. The extension has no access to `tx.origin` or any authenticated user field from the router.

## Impact Explanation

A curated pool protected by `SwapAllowlistExtension` is designed to restrict trading to a specific set of addresses (e.g., KYC'd counterparties, whitelisted market makers, or protocol-owned addresses). Once the router is allowlisted — the only way to let legitimate users trade through the standard periphery — the allowlist is completely bypassed. Any unprivileged EOA can call `router.exactInputSingle(pool, ...)` and execute a swap on the curated pool. This is a direct, complete loss of access-control integrity. Depending on the pool's purpose, consequences include unauthorized trading, unauthorized extraction of LP value, or violation of compliance constraints the pool was designed to enforce. The broken invariant is: `allowedSwapper[pool][bob] == false` yet bob successfully swaps.

**Severity: High** — the allowlist is a core security primitive; its complete bypass via the standard, public periphery entrypoint is a broken invariant with direct fund-impacting consequences.

## Likelihood Explanation

**High.** `MetricOmmSimpleRouter` is the standard periphery swap path. Any pool admin who wants to support normal user interaction through the router must allowlist it. The bypass requires no special privileges, no flash loans, and no unusual token behavior — a single call to `router.exactInputSingle` from any EOA is sufficient. The condition (router allowlisted) is the expected production configuration for any pool that uses both `SwapAllowlistExtension` and the router.

## Recommendation

The extension must check the real end-user identity, not the intermediary. Two sound approaches:

1. **Router forwards the real caller via `extensionData`:** `MetricOmmSimpleRouter` should encode `msg.sender` (the real user) into `extensionData` using transient storage (the pool already uses transient storage for callback context). `SwapAllowlistExtension` would then decode and check that field when `sender == router`. This preserves the extension interface and lets the router authenticate the real user.

2. **Wrapper extension with per-user sub-check:** Deploy a wrapper extension that, when `sender == router`, decodes the real user from `extensionData` and checks that address against the allowlist. This keeps the core extension unchanged but requires router cooperation.

Option 1 is the cleanest: it preserves the extension interface and fits the existing transient-storage pattern already used in `MetricOmmSwapRouterBase`.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][alice] = true   (alice is the only intended swapper)
  - allowedSwapper[pool][router] = true  (admin adds router so alice can use it)

Attack:
  1. bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, ..., recipient: bob})
  2. router calls pool.swap(bob, ...) — msg.sender to pool is router
  3. pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  5. Swap executes for bob despite bob never being allowlisted

Invariant broken:
  allowedSwapper[pool][bob] == false, yet bob successfully swaps on the curated pool.

Foundry test outline:
  function testBypassSwapAllowlist() public {
      // deploy pool + extension, allowlist alice and router
      // prank as bob (not allowlisted)
      vm.prank(bob);
      router.exactInputSingle(ExactInputSingleParams({pool: pool, ...}));
      // assert swap succeeded — bob received output tokens
  }
```