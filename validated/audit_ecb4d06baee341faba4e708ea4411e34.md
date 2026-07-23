Audit Report

## Title
Pool Admin Transfer Does Not Update Fee Destination, Enabling Accumulated Admin Fees to Be Drained to Stale Address — (`metric-core/contracts/MetricOmmPoolFactory.sol`)

## Summary

`acceptPoolAdmin` updates only `poolAdmin[pool]` but leaves `poolAdminFeeDestination[pool]` pointing to the old admin's address. Because `collectPoolFees` has no access-control modifier, any caller — including the old admin or a neutral MEV bot — can invoke it immediately after the transfer, routing all accumulated admin fees to the stale destination before the new admin can update it via `setPoolAdminFeeDestination`.

## Finding Description

`poolAdminFeeDestination[pool]` is written once at pool creation: [1](#0-0) 

`acceptPoolAdmin` updates only `poolAdmin[pool]` and clears `pendingPoolAdmin[pool]`; it never touches `poolAdminFeeDestination[pool]`: [2](#0-1) 

`collectPoolFees` carries only `nonReentrant` — no role check — and passes the stale destination directly to the pool: [3](#0-2) 

The only remedy for the new admin is a separate `setPoolAdminFeeDestination` call, which requires `onlyPoolAdmin` and is therefore a distinct transaction with no atomicity guarantee: [4](#0-3) 

There is no internal `collectFees` call inside `acceptPoolAdmin` to settle fees before the handover, and no mechanism to atomically update the destination at transfer time.

## Impact Explanation

All admin fees accumulated in the pool up to the moment of transfer are at risk of being routed to the old admin's address. Once `acceptPoolAdmin` is confirmed, any caller can invoke `collectPoolFees` in the same block, causing an irreversible loss of admin-fee principal for the new pool admin. This is a direct loss of owed protocol/admin fees meeting the Critical/High threshold under the allowed impact gate.

## Likelihood Explanation

The window opens on every pool admin transfer, which is a normal protocol operation. The old admin has a direct financial incentive to front-run `setPoolAdminFeeDestination` with `collectPoolFees`. Because `collectPoolFees` is fully permissionless, a neutral MEV searcher can also trigger it without any special role or setup. No special preconditions beyond a pending admin transfer are required, and the attack is repeatable across all pools.

## Recommendation

Inside `acceptPoolAdmin`, atomically settle accumulated fees to the current (old) destination before updating `poolAdmin[pool]`, then reset `poolAdminFeeDestination[pool]` to `address(0)` (or to a new-admin-supplied address) so the new admin starts with a clean slate:

```solidity
function acceptPoolAdmin(address pool) external override nonReentrant {
    address pending = pendingPoolAdmin[pool];
    if (pending == address(0)) revert NoPendingPoolAdminTransfer();
    if (msg.sender != pending) revert NotPendingPoolAdmin(pool, msg.sender, pending);

    // Settle all accrued fees to the old destination before handover
    PoolFeeConfig memory c = poolFeeConfig[pool];
    IMetricOmmPoolCollectFees(pool).collectFees(
        c.protocolSpreadFeeE6, c.adminSpreadFeeE6,
        c.protocolNotionalFeeE8, c.adminNotionalFeeE8,
        poolAdminFeeDestination[pool]
    );
    // Clear stale destination; new admin must set their own
    delete poolAdminFeeDestination[pool];

    address previousAdmin = poolAdmin[pool];
    poolAdmin[pool] = pending;
    delete pendingPoolAdmin[pool];
    emit PoolAdminTransferred(pool, previousAdmin, pending);
}
```

Alternatively, add an access-control modifier to `collectPoolFees` restricting it to the current pool admin or factory owner.

## Proof of Concept

1. Pool accumulates significant admin spread/notional fees over time.
2. Old admin calls `proposePoolAdminTransfer(pool, newAdmin)`.
3. New admin calls `acceptPoolAdmin(pool)` — `poolAdmin[pool]` is now `newAdmin`, but `poolAdminFeeDestination[pool]` still equals the old admin's address.
4. Old admin (or MEV bot) observes the `PoolAdminTransferred` event and calls `collectPoolFees(pool)` in the same block.
5. `collectPoolFees` routes all accumulated admin fees to `poolAdminFeeDestination[pool]` — the old admin's address — with no revert.
6. New admin calls `setPoolAdminFeeDestination(pool, newAdminDest)` — succeeds, but all previously accumulated fees are already drained.
7. New admin suffers a direct, irreversible loss equal to all admin fees accrued before the transfer.

### Citations

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L220-220)
```text
    poolAdminFeeDestination[pool] = params.adminFeeDestination;
```

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
