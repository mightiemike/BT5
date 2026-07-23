### Title
`protocolUnpausePool` Always Lands at Admin-Pause Level 1, Leaving Factory Owner Unable to Fully Restore Pool Operation — (File: `metric-core/contracts/MetricOmmPoolFactory.sol`)

---

### Summary

The factory owner's `protocolUnpausePool` unconditionally transitions the pool to pause level 1 (admin-pause) rather than level 0 (fully operational). Because only the pool admin can clear level 1, the factory owner (governance) cannot fully restore a pool to operational state without the pool admin's cooperation. A malicious or uncooperative pool admin can exploit this to permanently halt a pool, locking user liquidity.

---

### Finding Description

The protocol defines three pause levels:

| Level | Meaning | Who sets it |
|---|---|---|
| 0 | Fully operational | pool admin (`unpausePool`) |
| 1 | Admin pause | pool admin (`pausePool` / `unpausePool`) |
| 2 | Protocol pause | factory owner (`protocolPausePool` / `protocolUnpausePool`) |

`protocolPausePool` accepts level 0 **or** level 1 as a valid starting state: [1](#0-0) 

`protocolUnpausePool` always writes level 1, never level 0: [2](#0-1) 

`unpausePool` (the only path to level 0) is gated exclusively on `onlyPoolAdmin`: [3](#0-2) 

The factory owner has no function that writes level 0 directly. Admin-transfer is also pool-admin-initiated only: [4](#0-3) [5](#0-4) 

This creates two concrete broken invariants:

**Invariant 1 — Protocol pause/unpause cycle degrades pool state:**
A pool at level 0 that the factory owner protocol-pauses (0→2) and then protocol-unpauses (2→1) ends up at level 1 — a *worse* state than before the factory owner acted. The factory owner cannot correct this.

**Invariant 2 — Pool admin veto over governance:**
A pool admin who pauses (0→1) and then refuses to unpause leaves the pool permanently at level 1. The factory owner can escalate to level 2 (`protocolPausePool` accepts level 1 as input) but `protocolUnpausePool` only returns to level 1 — the factory owner is trapped in a 1→2→1→2 loop with no path to 0.

---

### Impact Explanation

When a pool is at level 1 (admin-pause), user-facing operations — swaps and liquidity add/remove — are blocked. LP positions cannot be exited and swap settlement cannot occur. This constitutes a loss of access to user principal and owed LP assets. The factory owner, who is the governance authority, cannot unilaterally restore the pool to operational state; they are dependent on the cooperation of the pool admin, a semi-trusted, lower-privilege role.

---

### Likelihood Explanation

The pool admin is the deployer of the pool — a semi-trusted party analogous to the "strategist" in the yAxis report. A pool admin who observes an impending admin transfer they disagree with, or who simply turns adversarial, can call `pausePool` and then refuse to call `unpausePool`. The factory owner's only recourse (`protocolPausePool` → `protocolUnpausePool`) returns the pool to level 1, not level 0. No external trigger or special condition is required beyond the pool admin calling a function they are already authorized to call.

---

### Recommendation

`protocolUnpausePool` should restore the pool to level 0 (fully operational), not level 1:

```solidity
function protocolUnpausePool(address pool) external override nonReentrant onlyOwner {
    (uint8 cur,,,,,) = PoolStateLibrary._slot0(pool);
    if (cur != 2) revert InvalidPauseTransition(cur, 0);
-   IMetricOmmPoolFactoryActions(pool).setPause(1);
+   IMetricOmmPoolFactoryActions(pool).setPause(0);
}
```

Alternatively, add a dedicated factory-owner function that can write level 0 directly, bypassing the admin-pause layer, so governance always retains the ability to fully restore a pool.

---

### Proof of Concept

```
// Scenario A: factory owner's own pause/unpause cycle degrades the pool
pool.pauseLevel == 0  (normal)
owner calls protocolPausePool(pool)   → pool.pauseLevel == 2
owner calls protocolUnpausePool(pool) → pool.pauseLevel == 1  ← stuck, NOT 0
owner cannot call unpausePool(pool)   → reverts NotPoolAdmin

// Scenario B: malicious pool admin permanent halt
poolAdmin calls pausePool(pool)       → pool.pauseLevel == 1
owner calls protocolPausePool(pool)   → pool.pauseLevel == 2
owner calls protocolUnpausePool(pool) → pool.pauseLevel == 1
// loop repeats; pool never reaches 0; user liquidity locked
``` [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L392-403)
```text
  function protocolPausePool(address pool) external override nonReentrant onlyOwner {
    (uint8 cur,,,,,) = PoolStateLibrary._slot0(pool);
    if (cur != 0 && cur != 1) revert InvalidPauseTransition(cur, 2);
    IMetricOmmPoolFactoryActions(pool).setPause(2);
  }

  /// @inheritdoc IMetricOmmPoolFactoryOwner
  function protocolUnpausePool(address pool) external override nonReentrant onlyOwner {
    (uint8 cur,,,,,) = PoolStateLibrary._slot0(pool);
    if (cur != 2) revert InvalidPauseTransition(cur, 1);
    IMetricOmmPoolFactoryActions(pool).setPause(1);
  }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L460-471)
```text
  function pausePool(address pool) external override nonReentrant onlyPoolAdmin(pool) {
    (uint8 cur,,,,,) = PoolStateLibrary._slot0(pool);
    if (cur != 0) revert InvalidPauseTransition(cur, 1);
    IMetricOmmPoolFactoryActions(pool).setPause(1);
  }

  /// @inheritdoc IMetricOmmPoolFactoryPoolAdmin
  function unpausePool(address pool) external override nonReentrant onlyPoolAdmin(pool) {
    (uint8 cur,,,,,) = PoolStateLibrary._slot0(pool);
    if (cur != 1) revert InvalidPauseTransition(cur, 0);
    IMetricOmmPoolFactoryActions(pool).setPause(0);
  }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L510-510)
```text
  function proposePoolAdminTransfer(address pool, address newAdmin) external override nonReentrant onlyPoolAdmin(pool) {
```

**File:** metric-core/contracts/interfaces/IMetricOmmPoolFactory/IMetricOmmPoolFactoryPoolAdmin.sol (L62-69)
```text
  /// @notice Start two-step admin transfer to `newAdmin`.
  function proposePoolAdminTransfer(address pool, address newAdmin) external;

  /// @notice Accept pending admin role for `pool` (must be pending admin).
  function acceptPoolAdmin(address pool) external;

  /// @notice Cancel a pending admin transfer for `pool`.
  function cancelPoolAdminTransfer(address pool) external;
```
