### Title
`setPoolAdminFeeDestination` Changes Fee Recipient Without Settling Accrued Fees First - (File: `metric-core/contracts/MetricOmmPoolFactory.sol`)

---

### Summary

`MetricOmmPoolFactory.setPoolAdminFeeDestination` updates `poolAdminFeeDestination[pool]` without first calling `collectFees`. Any spread-fee surplus and notional fees already accrued in the pool under the old destination are subsequently distributed to the new destination when `collectFees` is eventually triggered, permanently misallocating those fees.

---

### Finding Description

Two fee-parameter setters in `MetricOmmPoolFactory` correctly flush accrued fees before mutating state:

`setPoolAdminFees` (lines 408–435) calls `collectFees` with the **old** `poolAdminFeeDestination` before writing new fee rates: [1](#0-0) 

`setPoolProtocolFee` (lines 318–360) does the same: [2](#0-1) 

`setPoolAdminFeeDestination` (lines 438–447) does **not**: [3](#0-2) 

It writes the new destination directly with no prior `collectFees` call. Two categories of fees are already sitting in the pool at that moment:

1. **Spread fees** — the surplus `balance - binTotals - notionalFeeScaled` that accumulates from every swap's spread component.
2. **Notional fees** — `notionalFeeToken0Scaled` / `notionalFeeToken1Scaled` tracked explicitly in pool storage. [4](#0-3) 

When `collectFees` is next called (via `collectPoolFees`, `setPoolAdminFees`, or `setPoolProtocolFee`), it reads `adminFeeDestination_` from the factory's current mapping — now pointing to the **new** address — and transfers all accrued admin-share tokens there: [5](#0-4) 

Fees that accrued while the old destination was registered are irrecoverably sent to the new destination.

---

### Impact Explanation

The admin fee destination can be a separate entity from the pool admin (e.g., a DAO treasury, a multisig, or a third-party fee recipient). When the pool admin changes the destination, all previously accrued but uncollected admin fees — both spread and notional — are redirected to the new address. The old destination receives nothing for the period it was registered. This is a direct, quantifiable loss of owed fee assets to the prior recipient, matching the "direct loss of protocol fees" criterion.

---

### Likelihood Explanation

The pool admin is a valid, unprivileged-relative-to-owner actor who can call `setPoolAdminFeeDestination` at any time. Fees accrue continuously with every swap. Any admin fee destination change while the pool has active trading volume triggers the misallocation. No special conditions or malicious setup are required beyond normal pool operation.

---

### Recommendation

Collect accrued fees before updating the destination, mirroring the pattern already used in `setPoolAdminFees` and `setPoolProtocolFee`:

```solidity
function setPoolAdminFeeDestination(address pool, address newAdminFeeDestination)
    external override nonReentrant onlyPoolAdmin(pool)
{
    if (newAdminFeeDestination == address(0)) revert InvalidAdminFeeDestination();

    // Settle fees owed to the OLD destination before switching
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

1. Pool is deployed; `poolAdminFeeDestination[pool] = Alice`.
2. Traders execute swaps; spread-fee surplus and `notionalFeeToken0/1Scaled` accumulate in the pool.
3. Pool admin calls `setPoolAdminFeeDestination(pool, Bob)`. No `collectFees` is invoked; `poolAdminFeeDestination[pool]` is now `Bob`.
4. Anyone calls `collectPoolFees(pool)` (or the admin calls `setPoolAdminFees`). Inside `collectFees`, the admin share of all accrued fees — including those earned while Alice was the destination — is transferred to `Bob`.
5. Alice receives zero. Bob receives fees she was owed. The loss equals the full admin-share of spread and notional fees accrued between the last collection and the destination change. [6](#0-5) [7](#0-6)

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

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L417-425)
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

**File:** metric-core/contracts/MetricOmmPool.sol (L382-388)
```text
    uint256 notionalFee0AmountScaled = notionalFeeToken0Scaled;
    uint256 notionalFee1AmountScaled = notionalFeeToken1Scaled;

    uint256 surplus0Scaled =
      balance0() * TOKEN_0_SCALE_MULTIPLIER - uint256(binTotals.scaledToken0) - notionalFee0AmountScaled;
    uint256 surplus1Scaled =
      balance1() * TOKEN_1_SCALE_MULTIPLIER - uint256(binTotals.scaledToken1) - notionalFee1AmountScaled;
```

**File:** metric-core/contracts/MetricOmmPool.sol (L416-421)
```text
      if (totalFee0ToAdmin > 0) {
        transferToken0(adminFeeDestination_, totalFee0ToAdmin);
      }
      if (totalFee1ToAdmin > 0) {
        transferToken1(adminFeeDestination_, totalFee1ToAdmin);
      }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L429-430)
```text
      notionalFeeToken0Scaled = 0;
      notionalFeeToken1Scaled = 0;
```
