Looking at the repository structure, I need to trace the pool admin transfer flow and check whether all associated state (especially fee routing) is fully updated — the direct analog to M-11's `transferWork` only updating `work.creator`.

Let me read the critical functions. [1](#0-0) [2](#0-1) [3](#0-2) 

---

### Title
Pool Admin Transfer Does Not Update Fee Destination, Enabling Old Admin to Drain Accumulated Admin Fees — (`metric-core/contracts/MetricOmmPoolFactory.sol`)

---

### Summary

`acceptPoolAdmin` updates only `poolAdmin[pool]` but leaves `poolAdminFeeDestination[pool]` pointing to the old admin's address. Because `collectPoolFees` is permissionless (no access control), the old admin — or any third party — can immediately call it after the transfer to flush all accumulated admin fees to the stale destination before the new admin can update it.

---

### Finding Description

**Admin transfer path — partial state update:**

```solidity
// MetricOmmPoolFactory.sol L518-526
function acceptPoolAdmin(address pool) external override nonReentrant {
    address pending = pendingPoolAdmin[pool];
    if (pending == address(0)) revert NoPendingPoolAdminTransfer();
    if (msg.sender != pending) revert NotPendingPoolAdmin(pool, msg.sender, pending);
    address previousAdmin = poolAdmin[pool];
    poolAdmin[pool] = pending;          // ← only this is updated
    delete pendingPoolAdmin[pool];
    emit PoolAdminTransferred(pool, previousAdmin, pending);
    // poolAdminFeeDestination[pool] is NOT touched
}
```

`poolAdminFeeDestination[pool]` is set once at pool creation:

```solidity
// MetricOmmPoolFactory.sol L220
poolAdminFeeDestination[pool] = params.adminFeeDestination;
```

and is never reset during an admin transfer.

**Permissionless fee collection:**

```solidity
// MetricOmmPoolFactory.sol L379-389
function collectPoolFees(address pool) external override nonReentrant {
    PoolFeeConfig memory c = poolFeeConfig[pool];
    IMetricOmmPoolCollectFees(pool).collectFees(
        c.protocolSpreadFeeE6,
        c.adminSpreadFeeE6,
        c.protocolNotionalFeeE8,
        c.adminNotionalFeeE8,
        poolAdminFeeDestination[pool]   // ← stale old-admin destination
    );
}
```

`collectPoolFees` has **no access-control modifier** — any EOA or contract can call it at any time.

**The new admin's only remedy:**

```solidity
// MetricOmmPoolFactory.sol L438-447
function setPoolAdminFeeDestination(address pool, address newAdminFeeDestination)
    external override nonReentrant onlyPoolAdmin(pool)
{
    poolAdminFeeDestination[pool] = newAdminFeeDestination;
    ...
}
```

The new admin must call `setPoolAdminFeeDestination` in a separate transaction after `acceptPoolAdmin`. There is no atomic path to accept the admin role and update the fee destination simultaneously.

---

### Impact Explanation

All admin fees accumulated in the pool up to the moment of transfer are at risk. The moment `acceptPoolAdmin` is confirmed on-chain, the old admin (or any MEV bot) can call `collectPoolFees` in the same block, routing the entire accumulated admin-fee balance to the old admin's fee destination. The new admin receives nothing from the pre-transfer period and cannot recover those funds. This is a direct, irreversible loss of owed admin-fee principal for the new pool admin.

---

### Likelihood Explanation

Every pool admin transfer triggers the window. The old admin has a direct financial incentive to front-run `setPoolAdminFeeDestination` with `collectPoolFees`. Because `collectPoolFees` is permissionless, even a neutral MEV searcher can execute this without any special role. The attack requires no special setup beyond a pending admin transfer, which is a normal protocol operation.

---

### Recommendation

In `acceptPoolAdmin`, atomically reset `poolAdminFeeDestination[pool]` to `address(0)` (or to the new admin's address) and/or call `collectPoolFees` internally before updating `poolAdmin[pool]`, so that any accumulated fees are settled to the old destination only up to the point of transfer and the new admin starts with a clean slate:

```solidity
function acceptPoolAdmin(address pool) external override nonReentrant {
    address pending = pendingPoolAdmin[pool];
    if (pending == address(0)) revert NoPendingPoolAdminTransfer();
    if (msg.sender != pending) revert NotPendingPoolAdmin(pool, msg.sender, pending);

    // Settle fees to old destination before handing over admin rights
    _collectPoolFeesInternal(pool);

    address previousAdmin = poolAdmin[pool];
    poolAdmin[pool] = pending;
    delete pendingPoolAdmin[pool];
    emit PoolAdminTransferred(pool, previousAdmin, pending);
}
```

Alternatively, add access control to `collectPoolFees` so only the current pool admin or factory owner can trigger it, preventing the old admin from draining fees post-transfer.

---

### Proof of Concept

1. Pool accumulates significant admin spread/notional fees over time.
2. Old admin calls `proposePoolAdminTransfer(pool, newAdmin)`.
3. New admin calls `acceptPoolAdmin(pool)` — `poolAdmin[pool]` is now `newAdmin`, but `poolAdminFeeDestination[pool]` still equals old admin's address.
4. Old admin (or MEV bot) observes the `PoolAdminTransferred` event and immediately calls `collectPoolFees(pool)` in the same block or next block.
5. `collectPoolFees` routes all accumulated admin fees to `poolAdminFeeDestination[pool]` — the old admin's address — with no revert.
6. New admin calls `setPoolAdminFeeDestination(pool, newAdminDest)` — succeeds, but all previously accumulated fees are already gone.
7. New admin suffers a direct loss equal to all admin fees that had accrued before the transfer.

### Citations

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L379-389)
```text
  function collectPoolFees(address pool) external override nonReentrant {
    PoolFeeConfig memory c = poolFeeConfig[pool];
    IMetricOmmPoolCollectFees(pool)
      .collectFees(
        c.protocolSpreadFeeE6,
        c.adminSpreadFeeE6,
        c.protocolNotionalFeeE8,
        c.adminNotionalFeeE8,
        poolAdminFeeDestination[pool]
      );
  }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L438-447)
```text
  function setPoolAdminFeeDestination(address pool, address newAdminFeeDestination)
    external
    override
    nonReentrant
    onlyPoolAdmin(pool)
  {
    if (newAdminFeeDestination == address(0)) revert InvalidAdminFeeDestination();
    poolAdminFeeDestination[pool] = newAdminFeeDestination;
    emit PoolAdminFeeDestinationUpdated(pool, newAdminFeeDestination);
  }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L518-526)
```text
  function acceptPoolAdmin(address pool) external override nonReentrant {
    address pending = pendingPoolAdmin[pool];
    if (pending == address(0)) revert NoPendingPoolAdminTransfer();
    if (msg.sender != pending) revert NotPendingPoolAdmin(pool, msg.sender, pending);
    address previousAdmin = poolAdmin[pool];
    poolAdmin[pool] = pending;
    delete pendingPoolAdmin[pool];
    emit PoolAdminTransferred(pool, previousAdmin, pending);
  }
```
