### Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Allowing Any User to Bypass Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

The `SwapAllowlistExtension.beforeSwap` hook gates swaps by checking the `sender` parameter, which is `msg.sender` of the pool's `swap` call. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the end user. If the pool admin allowlists the router (required for normal router-based swaps), every user — including those not individually allowlisted — can bypass the curated pool's swap restriction by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` is defined as:

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

The pool calls this hook with `sender = msg.sender` of the `pool.swap(...)` invocation. When `MetricOmmSimpleRouter` executes a swap, it calls `pool.swap(...)` directly, making the router the `msg.sender`. The hook therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

The second parameter (the actual `recipient`, i.e., the end user) is unnamed and silently ignored by the extension. [2](#0-1) 

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks the `owner` parameter — the explicit beneficiary passed by the caller — rather than `msg.sender`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [3](#0-2) 

This asymmetry means the deposit allowlist correctly identifies the economic actor, while the swap allowlist does not.

The attack path:

1. Pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict swaps to a set of approved addresses.
2. Pool admin allowlists the `MetricOmmSimpleRouter` address so that approved users can swap via the router (a necessary operational step).
3. Any non-allowlisted user calls `MetricOmmSimpleRouter` → router calls `pool.swap(...)` → pool calls `beforeSwap(router, user, ...)` → extension checks `allowedSwapper[pool][router]` → **passes**, because the router is allowlisted.
4. The non-allowlisted user's swap executes on the curated pool, bypassing the intended access control.

This matches the "wrong-actor binding" invariant identified in the audit scope:

> *"Every guard must key authorization to the same actor that the economic action is actually attributed to."* [4](#0-3) 

---

### Impact Explanation

A curated pool's swap allowlist is completely ineffective for router-based swaps once the router is allowlisted. Any unprivileged user can trade on a pool that was intended to be restricted to a specific set of counterparties. Depending on the pool's purpose (e.g., institutional-only liquidity, KYC-gated pools, or pools with specific LP agreements), this allows unauthorized extraction of LP assets at oracle-derived prices, breaking the pool's curation invariant and potentially causing direct loss to LPs who deposited under the assumption that only approved counterparties could trade against them.

---

### Likelihood Explanation

The trigger requires no special privileges. Any user with knowledge of the router address can route a swap through `MetricOmmSimpleRouter`. The only precondition is that the pool admin has allowlisted the router — a step that is operationally necessary for the router to function at all on a curated pool. The bypass is therefore reachable on any curated pool that supports router-based swaps.

---

### Recommendation

The `beforeSwap` hook should receive and check the **end user's identity**, not the intermediary router's address. Two approaches:

1. **Pass the end user through `extensionData`**: The router encodes the actual user address into the `extensionData` bytes passed to `pool.swap(...)`, and the extension decodes and checks it. This requires the extension to trust that the router correctly reports the user, which introduces a trust assumption on the router.

2. **Check `recipient` instead of `sender`**: If the pool's `swap` call passes the end user as `recipient`, the extension should check `allowedSwapper[pool][recipient]` rather than `allowedSwapper[pool][sender]`. This is the simpler fix and mirrors how `DepositAllowlistExtension` correctly checks `owner` rather than `msg.sender`.

The cleanest fix is option 2 — align `SwapAllowlistExtension` with `DepositAllowlistExtension` by gating on the economic beneficiary (`recipient`) rather than the intermediary caller (`sender`).

---

### Proof of Concept

```solidity
// Pool admin sets up a curated pool: only `approvedUser` may swap
swapExtension.setAllowedToSwap(address(pool), approvedUser, true);
// Admin also allowlists the router so approvedUser can use it
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// attacker (not in allowlist) bypasses the guard via the router
vm.startPrank(attacker);
token0.approve(address(router), type(uint256).max);
// router.swap(...) → pool.swap(msg.sender=router, recipient=attacker, ...)
// beforeSwap checks allowedSwapper[pool][router] → true → swap executes
router.swap(address(pool), attacker, false, int128(1000), type(uint128).max, "");
vm.stopPrank();
// attacker successfully swapped on a pool they were not allowlisted for
``` [5](#0-4)

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

**File:** generate_scanned_questions.py (L718-724)
```python
        Vector(
            title="wrong-actor binding",
            question_focus="the hook checks the wrong actor among sender, owner, payer, or recipient",
            exploit="Separate payer from owner or route through the router so the extension sees a different actor than the protocol intended to gate.",
            invariant="Every guard must key authorization to the same actor that the economic action is actually attributed to.",
            impact="High direct loss or policy bypass on curated pools.",
        ),
```
