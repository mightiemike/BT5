### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any Disallowed Swapper to Bypass the Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks the router's address against the allowlist rather than the actual end user's address. A pool admin who intends to gate swaps to a curated set of addresses can be bypassed by any disallowed user who routes through the public router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces the allowlist as follows:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

- `msg.sender` is the pool — correctly used to namespace the allowlist per pool.
- `sender` is the first argument the pool passes into the extension hook — this is `msg.sender` of the pool's own `swap()` call.

When a user calls `MetricOmmSimpleRouter` (the public periphery router), the router calls `pool.swap(...)`. At that point, `msg.sender` inside the pool is the **router address**, not the end user. The pool forwards this router address as `sender` to the extension. The extension therefore checks whether the **router** is allowlisted, not whether the **end user** is allowlisted. [2](#0-1) 

This creates a binary failure mode:

1. **Router not allowlisted** → every router-mediated swap reverts for all users, including legitimately allowlisted ones. Core swap functionality is broken for the standard periphery path.
2. **Router allowlisted** (the only way to restore router functionality) → the allowlist is completely bypassed: any disallowed user can swap by routing through the public router.

The same structural problem exists in `DepositAllowlistExtension.beforeAddLiquidity`, which checks `owner` — a value that `MetricOmmPoolLiquidityAdder` can set independently of the actual payer/caller, creating an analogous owner-vs-payer actor mismatch. [3](#0-2) 

---

### Impact Explanation

A curated pool using `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., KYC'd addresses, institutional partners, or protocol-controlled accounts) provides **zero enforcement** once the public router is allowlisted. Any address — including adversarial MEV bots or unauthorized retail users — can execute swaps against the pool's oracle-priced bins by calling `MetricOmmSimpleRouter`. This constitutes:

- **Broken core pool functionality**: the allowlist guard silently fails open on the standard periphery path.
- **Direct loss of user principal / LP assets**: unauthorized swappers can drain arbitrage value from LP positions that were intended to be protected behind a curated gate.
- **Admin-boundary break**: the pool admin's configured access control is bypassed by an unprivileged path (the public router).

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the **primary public entrypoint** for swaps; most users and integrations will use it rather than calling the pool directly.
- The pool admin must allowlist the router to make the pool usable at all through the standard periphery, which is the natural operational choice.
- No special privileges, flash loans, or unusual token behavior are required — any EOA can call the router.
- The bypass is **always active** once the router is allowlisted; it requires no timing, no oracle manipulation, and no multi-step setup.

---

### Recommendation

The pool must pass the **originating user** as `sender` to the extension, not the immediate `msg.sender`. Two complementary fixes:

1. **In `MetricOmmSimpleRouter`**: pass the router's caller (`msg.sender` of the router call) as the `sender` argument to `pool.swap()`, so the pool forwards the real end user to the extension.
2. **In `SwapAllowlistExtension.beforeSwap`**: document (or enforce via interface) that `sender` must be the economic actor, not an intermediary. Optionally, add a secondary check against a `tx.origin`-equivalent passed through `extensionData` for defense-in-depth (though `tx.origin` has its own caveats).

For `DepositAllowlistExtension`, ensure `MetricOmmPoolLiquidityAdder` passes the actual payer/caller as `owner`, or gate on the payer separately.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, ALICE, true).
  - Pool admin does NOT allowlist BOB.
  - Pool admin calls setAllowedToSwap(pool, address(router), true)
    (required to allow ALICE to use the router at all).

Attack:
  1. BOB calls MetricOmmSimpleRouter.exactInput(...) targeting the pool.
  2. Router calls pool.swap(...) — pool's msg.sender = router.
  3. Pool calls extension.beforeSwap(sender=router, ...).
  4. Extension checks: allowedSwapper[pool][router] == true → passes.
  5. BOB's swap executes against the curated pool's oracle-priced bins.

Expected: revert NotAllowedToSwap for BOB.
Actual:   swap succeeds; BOB bypasses the allowlist entirely.
```

The invariant broken: every configured hook must gate the **economically relevant actor** — the address whose trade is being settled — not the intermediary contract that forwarded the call. [1](#0-0) [3](#0-2)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
