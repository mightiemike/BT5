### Title
`setPoolAdminFeeDestination` Redirects All Accumulated Fees to New Destination Without Prior Collection — (File: `metric-core/contracts/MetricOmmPoolFactory.sol`)

---

### Summary

`MetricOmmPoolFactory.setPoolAdminFeeDestination` updates `poolAdminFeeDestination[pool]` without first flushing accumulated fees to the old destination. Every other fee-config mutator in the factory (`setPoolAdminFees`, `setPoolProtocolFee`) explicitly calls `collectFees` with the old config before overwriting it. The missing flush in `setPoolAdminFeeDestination` lets the pool admin silently redirect all previously accumulated admin fees — both notional and spread — to an arbitrary new address, stealing them from the original `adminFeeDestination`.

---

### Finding Description

**Consistent pattern in fee-update functions (correct):**

`setPoolAdminFees` reads the old config, calls `collectFees` with the old destination, then overwrites:

```solidity
// MetricOmmPoolFactory.sol L417-L429
PoolFeeConfig memory c = poolFeeConfig[pool];
IMetricOmmPoolCollectFees(pool).collectFees(
    c.protocolSpreadFeeE6,
    c.adminSpreadFeeE6,
    c.protocolNotionalFeeE8,
    c.adminNotionalFeeE8,
    poolAdminFeeDestination[pool]   // ← old destination used before overwrite
);
c.adminSpreadFeeE6 = newAdminSpreadFeeE6;
c.adminNotionalFeeE8 = newAdminNotionalFeeE8;
poolFeeConfig[pool] = c;
```

`setPoolProtocolFee` (L327-L354) follows the identical flush-then-overwrite pattern.

**Missing flush in `setPoolAdminFeeDestination` (vulnerable):**

```solidity
// MetricOmmPoolFactory.sol L438-L447
function setPoolAdminFeeDestination(address pool, address newAdminFeeDestination)
    external override nonReentrant onlyPoolAdmin(pool)
{
    if (newAdminFeeDestination == address(0)) revert InvalidAdminFeeDestination();
    poolAdminFeeDestination[pool] = newAdminFeeDestination;   // ← no collectFees first
    emit PoolAdminFeeDestinationUpdated(pool, newAdminFeeDestination);
}
```

**How fees are distributed on collection:**

`collectPoolFees` (L379-L389) reads the current `poolAdminFeeDestination[pool]` and passes it straight into `collectFees`:

```solidity
// MetricOmmPoolFactory.sol L379-L389
function collectPoolFees(address pool) external override nonReentrant {
    PoolFeeConfig memory c = poolFeeConfig[pool];
    IMetricOmmPoolCollectFees(pool).collectFees(
        c.protocolSpreadFeeE6, c.adminSpreadFeeE6,
        c.protocolNotionalFeeE8, c.adminNotionalFeeE8,
        poolAdminFeeDestination[pool]   // ← whatever is stored NOW
    );
}
```

Inside `MetricOmmPool.collectFees` (L364-L434), ALL accumulated admin fees — notional fees stored in `notionalFeeToken0Scaled`/`notionalFeeToken1Scaled` and the entire spread-fee surplus — are transferred to `adminFeeDestination_` in a single call. There is no per-epoch or per-destination accounting; the entire balance is swept to whichever address is current at collection time.

**Attack sequence:**

1. Pool is live; fees accumulate in the pool for the original `adminFeeDestination` (e.g., a revenue-sharing contract or a third-party LP treasury).
2. Pool admin calls `setPoolAdminFeeDestination(pool, attackerAddress)` — no flush occurs.
3. Anyone (or the admin) calls `collectPoolFees(pool)`.
4. All accumulated admin fees — both notional and spread — are sent to `attackerAddress` instead of the original destination.
5. The original `adminFeeDestination` receives nothing for the entire accumulation period.

---

### Impact Explanation

Direct loss of accumulated admin fees for the original `adminFeeDestination`. The pool admin can redirect any quantity of fees that have built up since the last collection. Because `collectPoolFees` is permissionless, the admin can also time the call to maximise the redirected amount. Both token0 and token1 admin fees are affected simultaneously.

---

### Likelihood Explanation

The pool admin is a single EOA or multisig with no timelock on `setPoolAdminFeeDestination`. The function is callable at any time. The only prerequisite is that fees have accumulated since the last collection, which is the normal operating state of any active pool. No external conditions or oracle manipulation are required.

---

### Recommendation

Apply the same flush-then-overwrite pattern used by `setPoolAdminFees` and `setPoolProtocolFee`:

```solidity
function setPoolAdminFeeDestination(address pool, address newAdminFeeDestination)
    external override nonReentrant onlyPoolAdmin(pool)
{
    if (newAdminFeeDestination == address(0)) revert InvalidAdminFeeDestination();

    // Flush accumulated fees to the OLD destination before overwriting
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

```
State:
  poolAdminFeeDestination[pool] = ORIGINAL_DEST
  notionalFeeToken0Scaled       = 1000e18   (accumulated over N swaps)
  notionalFeeToken1Scaled       = 500e18
  spread surplus (token0)       = 200e18

Step 1 — admin calls:
  factory.setPoolAdminFeeDestination(pool, ATTACKER)
  → poolAdminFeeDestination[pool] = ATTACKER
  → no collectFees called

Step 2 — anyone calls:
  factory.collectPoolFees(pool)
  → pool.collectFees(..., ATTACKER)
  → ATTACKER receives all admin share of 1000e18 token0 + 500e18 token1 notional fees
     plus admin share of 200e18 token0 spread surplus
  → ORIGINAL_DEST receives 0
```

**Relevant code locations:**

- `setPoolAdminFeeDestination` (missing flush): [1](#0-0) 
- `setPoolAdminFees` (correct flush pattern for comparison): [2](#0-1) 
- `collectPoolFees` (reads current destination at collection time): [3](#0-2) 
- `MetricOmmPool.collectFees` (sweeps all accumulated fees to the passed destination): [4](#0-3)

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

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L417-429)
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

    c.adminSpreadFeeE6 = newAdminSpreadFeeE6;
    c.adminNotionalFeeE8 = newAdminNotionalFeeE8;
    poolFeeConfig[pool] = c;
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

**File:** metric-core/contracts/MetricOmmPool.sol (L416-430)
```text
      if (totalFee0ToAdmin > 0) {
        transferToken0(adminFeeDestination_, totalFee0ToAdmin);
      }
      if (totalFee1ToAdmin > 0) {
        transferToken1(adminFeeDestination_, totalFee1ToAdmin);
      }
      if (totalFee0ToProtocol > 0) {
        transferToken0(FACTORY, totalFee0ToProtocol);
      }
      if (totalFee1ToProtocol > 0) {
        transferToken1(FACTORY, totalFee1ToProtocol);
      }

      notionalFeeToken0Scaled = 0;
      notionalFeeToken1Scaled = 0;
```
