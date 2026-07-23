### Title
`setPoolAdminFeeDestination` Redirects Accrued-But-Uncollected Admin Fees to New Destination Without Prior Settlement — (`metric-core/contracts/MetricOmmPoolFactory.sol`)

---

### Summary

`setPoolAdminFeeDestination` updates `poolAdminFeeDestination[pool]` without first calling `collectFees`. All spread-fee surplus and notional fees already accrued in the pool are subsequently paid to the **new** destination on the next `collectFees` call, permanently depriving the **old** destination of fees it had already earned.

---

### Finding Description

Every swap accrues two categories of admin fees inside `MetricOmmPool`:

1. **Spread fees** — the token surplus sitting in the pool (`balance - binTotals - notionalFeeScaled`). These are real tokens already in the contract, owned by fee recipients in proportion to their configured share.
2. **Notional fees** — tracked explicitly in `notionalFeeToken0Scaled` / `notionalFeeToken1Scaled`.

Both are paid out to `poolAdminFeeDestination[pool]` only when `collectFees` is called.

The factory exposes two fee-change paths. Both `setPoolAdminFees` and `setPoolProtocolFee` call `collectFees` **before** mutating state, flushing all accrued fees to the current destination first: [1](#0-0) [2](#0-1) 

`setPoolAdminFeeDestination` performs **no such flush**: [3](#0-2) 

After the call, `poolAdminFeeDestination[pool]` points to the new address. The next `collectFees` invocation — whether triggered by the owner, the pool admin, or anyone calling the public `collectPoolFees` — distributes all previously accrued fees to the new destination: [4](#0-3) 

The old destination receives nothing for the period it was entitled to.

---

### Impact Explanation

The old `adminFeeDestination` permanently loses all spread-fee surplus and notional fees that accrued while it was the configured recipient. These are real ERC-20 tokens already held by the pool. The loss is bounded by the total admin-fee share of all swap volume since the last `collectFees` call — potentially large for high-volume pools with long collection intervals. This is a direct loss of owed LP/fee assets to a legitimate recipient.

---

### Likelihood Explanation

The pool admin is a semi-trusted role that can legitimately call `setPoolAdminFeeDestination`. The old fee destination is a **separate address** from the pool admin (set independently at pool creation via `params.adminFeeDestination`). A pool admin wishing to redirect accrued fees — whether maliciously or simply unaware of the missing flush — can do so at any time with a single transaction. No timelock, no guard, and no off-chain coordination is required. The public `collectPoolFees` function means anyone can trigger the payout to the new destination immediately after the destination change.

---

### Recommendation

Mirror the pattern used by `setPoolAdminFees` and `setPoolProtocolFee`: call `collectFees` with the **current** config and destination before overwriting `poolAdminFeeDestination[pool]`.

```solidity
function setPoolAdminFeeDestination(address pool, address newAdminFeeDestination)
    external
    override
    nonReentrant
    onlyPoolAdmin(pool)
{
    if (newAdminFeeDestination == address(0)) revert InvalidAdminFeeDestination();

    // Flush accrued fees to the OLD destination before switching.
    PoolFeeConfig memory c = poolFeeConfig[pool];
    IMetricOmmPoolCollectFees(pool).collectFees(
        c.protocolSpreadFeeE6,
        c.adminSpreadFeeE6,
        c.protocolNotionalFeeE8,
        c.adminNotionalFeeE8,
        poolAdminFeeDestination[pool]   // old destination
    );

    poolAdminFeeDestination[pool] = newAdminFeeDestination;
    emit PoolAdminFeeDestinationUpdated(pool, newAdminFeeDestination);
}
```

---

### Proof of Concept

1. Pool is deployed; `adminFeeDestination = Alice`, `poolAdmin = Bob`.
2. Many swaps occur; spread-fee surplus accumulates (e.g., 1 000 USDC owed to Alice).
3. Bob calls `setPoolAdminFeeDestination(pool, Carol)`. No `collectFees` is triggered.
4. Anyone calls `collectPoolFees(pool)`. The factory reads `poolAdminFeeDestination[pool] == Carol` and transfers the 1 000 USDC to Carol.
5. Alice receives 0 USDC despite having earned the fees during her tenure as fee destination.

The root cause is the missing `collectFees` call in `setPoolAdminFeeDestination`, directly analogous to the M-14 pattern where a replacement of a key contract/address without prior settlement of accrued state causes permanent loss to the prior entitled party. [3](#0-2)

### Citations

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L327-335)
```text
    PoolFeeConfig memory c = poolFeeConfig[pool];
    IMetricOmmPoolCollectFees(pool)
      .collectFees(
        c.protocolSpreadFeeE6,
        c.adminSpreadFeeE6,
        c.protocolNotionalFeeE8,
        c.adminNotionalFeeE8,
        poolAdminFeeDestination[pool]
      );
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

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L417-426)
```text
    PoolFeeConfig memory c = poolFeeConfig[pool];
    IMetricOmmPoolCollectFees(pool)
      .collectFees(
        c.protocolSpreadFeeE6,
        c.adminSpreadFeeE6,
        c.protocolNotionalFeeE8,
        c.adminNotionalFeeE8,
        poolAdminFeeDestination[pool]
      );

```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L437-447)
```text
  /// @inheritdoc IMetricOmmPoolFactoryPoolAdmin
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
