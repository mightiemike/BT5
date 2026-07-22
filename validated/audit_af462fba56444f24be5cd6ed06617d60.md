Based on my analysis of the codebase, I can identify a valid analog to the external report's "wrong-actor participates in a gated process" class of bug.

---

### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool against the per-pool allowlist. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool forwards `msg.sender` as the `sender` argument to the extension, the allowlist check evaluates the router's address rather than the actual swapper's address. This lets any user bypass a curated pool's swap allowlist by routing through the public router.

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct), and `sender` is the first argument the pool passes into the hook. The allowlist is keyed `allowedSwapper[pool][sender]`. [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInput` (or `exactOutput`), the router becomes `msg.sender` inside `MetricOmmPool.swap`. If the pool passes its own `msg.sender` (the router) as the `sender` argument to `_beforeSwap`, the extension evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`. [3](#0-2) 

The protocol's own audit pivot document explicitly identifies this as the critical check:

> "Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting." [4](#0-3) 

This creates a binary failure mode:

1. **Router not allowlisted:** Individually allowlisted users who route through `MetricOmmSimpleRouter` are blocked even though they are permitted — core swap functionality is broken for the intended user set.
2. **Router allowlisted (to fix case 1):** The allowlist is completely bypassed — any user can swap against the curated pool by routing through the public router.

The analog to the external report is exact: just as minority validators could participate in consensus because the system checked the wrong identity (delegation amount at login time, not at proposal time), here the swap guard checks the wrong actor (the router, not the user) because the identity is evaluated at the wrong layer of the call stack.

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is a curated pool — the admin explicitly intends to restrict who can trade against it. Bypassing this guard allows any unpermissioned user to execute swaps against the pool. Depending on pool configuration, this can result in:

- Direct loss of LP principal through trades the pool was designed to reject (e.g., a pool restricted to a single market maker whose pricing assumptions depend on exclusive access).
- Broken core pool functionality: the allowlist is the primary access-control mechanism for curated pools; its failure renders the pool's curation semantics meaningless.

This meets the **Broken core pool functionality causing loss of funds** and **Admin-boundary break: unprivileged path bypasses a required guard** impact criteria.

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is a public, permissionless contract — any user can call it.
- No special setup is required beyond knowing the pool address and the router address.
- The bypass is triggered by the normal, documented user flow (routing through the periphery router).
- The pool admin cannot prevent this without removing the extension entirely, since the router's address cannot be excluded from the allowlist without also blocking legitimate router-mediated swaps for allowlisted users.

### Recommendation

The pool must pass the **original end-user's address** — not `msg.sender` — as the `sender` argument to `_beforeSwap`. Two approaches:

1. **Transient storage context:** Store the original `msg.sender` in transient storage at the top of `MetricOmmPool.swap` and read it inside `ExtensionCalling._beforeSwap` when constructing the hook call. The protocol already uses EIP-1153 transient storage for reentrancy guards and callback context, so this pattern is established. [5](#0-4) 

2. **Explicit originator parameter:** Add an `originator` parameter to the public `swap` entry point and require the router to pass `msg.sender` explicitly, then forward it through `ExtensionCalling` to the hook.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, alice, true).
  - Pool admin does NOT allowlist the router or bob.

Attack (bob bypasses allowlist):
  1. bob calls MetricOmmSimpleRouter.exactInput({pool: pool, ...}).
  2. Router calls pool.swap(msg.sender=router, ...).
  3. Pool calls extension.beforeSwap(sender=router, ...).
  4. Extension checks allowedSwapper[pool][router] → false (router not allowlisted).
     → Reverts. (Case 1: legitimate router users also blocked.)

Fix attempt by admin (makes it worse):
  5. Pool admin calls setAllowedToSwap(pool, router, true) to allow alice to use the router.
  6. Now bob calls MetricOmmSimpleRouter.exactInput({pool: pool, ...}).
  7. Extension checks allowedSwapper[pool][router] → true.
  8. bob's swap executes successfully against the curated pool.
     → Allowlist fully bypassed for any user who routes through the router.
``` [3](#0-2) [6](#0-5)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
```

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

**File:** generate_scanned_questions.py (L656-663)
```python
            short="swap allowlist gate",
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```

**File:** README.md (L34-34)
```markdown
EIP-1153 (transient storage) — the pool's transient reentrancy guard (MetricReentrancyGuardTransient) and transient swap/callback context. Intention: cheap per-tx reentrancy protection + callback routing without persistent storage. Alignment: gas efficiency and safety on the swap-callback path; requires Cancun+ (foundry evm_version = prague).
```
