### Title
`SwapAllowlistExtension` Checks Immediate Pool Caller (Router) Instead of Actual End-User, Enabling Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

The `SwapAllowlistExtension.beforeSwap` hook gates swap access by checking the `sender` parameter passed by the pool. The pool sets `sender` to `msg.sender` — the immediate caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the router is the immediate caller, so the extension checks the **router's address** against the allowlist, not the actual end-user's address. This creates an inescapable dilemma for pool admins: either allowlisted users cannot use the router at all (broken core functionality), or the admin must allowlist the router — at which point **any user** can bypass the allowlist by routing through it.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension caller) and `sender` is the first argument the pool passes into the hook. The pool sets `sender = msg.sender` of the `pool.swap()` call — i.e., the **immediate caller of the pool**, not the original end-user.

This is confirmed by the integration test in `FullMetricExtensionTest`:

```solidity
// metric-periphery/test/extensions/FullMetricExtension.t.sol:70-73
swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);
// ...
_swap(0, users[0], false, int128(1000), type(uint128).max);
```

The test allowlists `callers[0]` — the `TestCaller` intermediary contract — **not** `users[0]` (the actual human user). The swap succeeds because the pool passes the `TestCaller`'s address (the immediate caller) as `sender` to the extension. `users[0]` is never checked.

`MetricOmmSimpleRouter` is the production analog of `TestCaller`. When a user calls `router.exactInput(...)`, the router calls `pool.swap(...)`. The pool's `msg.sender` is the router, so the extension receives `sender = router`. The allowlist lookup becomes `allowedSwapper[pool][router]`.

**Two failure modes result:**

| Scenario | Outcome |
|---|---|
| Router **not** allowlisted | Allowlisted users cannot use the router — core periphery path broken |
| Router **is** allowlisted (admin enables router-mediated swaps) | Any user, including those explicitly excluded, can bypass the allowlist by routing through the router |

The second scenario is the direct analog to the vault bug: just as any SPL token account with `owner == state.key()` and `mint == state.belo_mint` passes the vault check regardless of whether it is the canonical ATA, any swap routed through the allowlisted router passes the `SwapAllowlistExtension` check regardless of who the actual end-user is.

---

### Impact Explanation

On any pool that (a) configures `SwapAllowlistExtension` to restrict swap access to specific addresses and (b) allowlists the router to support the standard periphery path, **the allowlist is completely ineffective**. Any unprivileged user can execute swaps on a curated pool by routing through `MetricOmmSimpleRouter`. This constitutes a direct admin-boundary break: the pool admin's configured access control is bypassed by an unprivileged path (the public router). If the pool is designed to restrict trading to KYC'd counterparties, institutional LPs, or specific strategies, the protection is nullified.

---

### Likelihood Explanation

High. `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint. Pool admins who deploy allowlisted pools and want their allowlisted users to be able to use the standard router **must** allowlist the router address — there is no other supported path. The mistake is structurally forced by the design. Any user who observes that the router address is in the allowlist (readable on-chain) can immediately exploit the bypass.

---

### Recommendation

The pool must forward the **original end-user's address** — not the router's address — as `sender` to the extension. Two complementary fixes:

1. **Router-side**: `MetricOmmSimpleRouter` should accept an explicit `sender` parameter (the original `msg.sender` of the router call) and pass it to `pool.swap()` as the `sender` argument, rather than relying on the pool to use `msg.sender`.

2. **Extension-side**: `SwapAllowlistExtension` should document that `sender` must be the economic actor, and the pool interface should enforce that the `sender` argument to `swap()` is the address the pool will attribute the trade to — not necessarily `msg.sender`.

The invariant to enforce: **the identity checked by the allowlist must be the same identity that the pool attributes the economic action to**, regardless of which supported public entrypoint reaches the pool.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — Alice is the only allowed swapper.
3. Pool admin also calls `setAllowedToSwap(pool, router, true)` — necessary so Alice can use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInput(...)` targeting the pool.
5. The router calls `pool.swap(...)`. Inside the pool, `msg.sender == router`.
6. The pool calls `extension.beforeSwap(router, ...)`.
7. The extension evaluates `allowedSwapper[pool][router]` → `true`.
8. Bob's swap executes successfully — the allowlist is bypassed.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
