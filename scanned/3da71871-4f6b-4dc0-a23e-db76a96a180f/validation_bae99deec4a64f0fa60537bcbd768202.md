### Title
Pool Admin Can Front-Run Swaps via Immediate Fee Changes Without Timelock — (File: `metric-core/contracts/MetricOmmPoolFactory.sol`)

---

### Summary

`setPoolAdminFees()` and `setPoolBinAdditionalFees()` allow the pool admin to change fee parameters with immediate effect and no timelock. The price-provider change path correctly enforces a configurable `priceProviderTimelock`, but no equivalent delay exists for fee changes. A pool admin can front-run any pending swap by spiking fees to the cap, collecting the inflated fee, then restoring the original rate — all within the same block.

---

### Finding Description

`MetricOmmPoolFactory.setPoolAdminFees()` updates `adminSpreadFeeE6` and `adminNotionalFeeE8` and immediately pushes the new combined fee to the pool via `setPoolFees()`:

```solidity
// metric-core/contracts/MetricOmmPoolFactory.sol  L408-L434
function setPoolAdminFees(address pool, uint24 newAdminSpreadFeeE6, uint24 newAdminNotionalFeeE8)
    external override nonReentrant onlyPoolAdmin(pool)
{
    if (newAdminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    if (newAdminNotionalFeeE8 > maxAdminNotionalFeeE8) revert AdminFeeTooHigh();
    ...
    IMetricOmmPoolFactoryActions(pool)
      .setPoolFees(c.protocolSpreadFeeE6 + c.adminSpreadFeeE6,
                   c.protocolNotionalFeeE8 + c.adminNotionalFeeE8);
```

`setPoolBinAdditionalFees()` similarly pushes per-bin `addFeeBuyE6` / `addFeeSellE6` to the pool immediately:

```solidity
// metric-core/contracts/MetricOmmPoolFactory.sol  L450-L457
function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external override nonReentrant onlyPoolAdmin(pool)
{
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
}
```

By contrast, the price-provider change path enforces a mandatory waiting period:

```solidity
// metric-core/contracts/MetricOmmPoolFactory.sol  L487-L489
uint256 executeAfter = block.timestamp + timelock;
pendingPriceProvider[pool] = newPriceProvider;
pendingPriceProviderExecuteAfter[pool] = executeAfter;
```

```solidity
// metric-core/contracts/MetricOmmPoolFactory.sol  L499
if (block.timestamp < execAfter) revert PriceProviderTimelockNotElapsed(execAfter, block.timestamp);
```

No analogous two-step / timelock mechanism exists for fee changes. The hard caps are:

```solidity
// metric-core/contracts/MetricOmmPoolFactory.sol  L44-L45
uint24 internal constant HARD_MAX_SPREAD_FEE_E6 = 200_000;   // 20 %
uint24 internal constant HARD_MAX_NOTIONAL_FEE_E8 = 1_000_000; // 1 %
```

These caps are the only bound on how high the pool admin can push fees, and they take effect in the same transaction.

---

### Impact Explanation

A pool admin who observes a large swap in the mempool can:

1. Call `setPoolAdminFees(pool, maxAdminSpreadFeeE6, maxAdminNotionalFeeE8)` — fees jump to the cap (up to 20 % spread + 1 % notional) with no delay.
2. The victim's swap executes at the inflated rate; the extra fee accrues to the pool and is later collected by the admin via `collectPoolFees`.
3. Call `setPoolAdminFees(pool, originalSpread, originalNotional)` — fees restored.

The pool's `swap()` reads `spreadFeeE6` and `notionalFeeE8` from live storage at execution time:

```solidity
// metric-core/contracts/MetricOmmPool.sol  L546-L548
uint256 nf = notionalFeeE8;
buyPriceX64 = Math.mulDiv(askBeforeNotional, 1e8, 1e8 - nf, Math.Rounding.Ceil).toUint128();
sellPriceX64 = Math.mulDiv(bidAfterSpread, 1e8 - nf, 1e8, Math.Rounding.Floor).toUint128();
```

There is no snapshot of the fee at the time the user signed their transaction. The user receives less output (or pays more input) than the fee rate visible when they submitted the transaction. This is a direct loss of user principal on every swap that is front-run.

---

### Likelihood Explanation

Medium. The pool admin is a semi-trusted role set at pool creation. Users have no on-chain mechanism to verify that the pool admin will not front-run them, and no advance notice is broadcast before a fee change takes effect. Any pool whose admin is not a time-locked governance contract is exposed. The attack requires only standard mempool visibility and two sequential transactions (or a single bundle on networks with private mempools).

---

### Recommendation

Apply the same two-step timelock pattern already used for price-provider changes to fee changes:

1. Introduce `proposeFeeChange(pool, newAdminSpreadFeeE6, newAdminNotionalFeeE8)` that records the pending values and a `block.timestamp + feeChangeTimelock` deadline.
2. Introduce `executeFeeChange(pool)` that enforces the deadline before calling `setPoolFees`.
3. Enforce a protocol-level minimum timelock (e.g., 24 hours) so pool creators cannot opt out of the delay.
4. Apply the same pattern to `setPoolBinAdditionalFees`.

---

### Proof of Concept

```
Block N   (mempool): User broadcasts swap(pool, zeroForOne=true, amountSpecified=1_000_000e6, ...)
Block N   (front):   PoolAdmin → setPoolAdminFees(pool, 200_000, 1_000_000)
                     → pool.setPoolFees(protocolSpread + 200_000, protocolNotional + 1_000_000)
Block N   (victim):  User's swap executes; spreadFeeE6 = 200_000 (20%), notionalFeeE8 = 1_000_000 (1%)
                     User receives ~21% less token1 than expected
Block N+1 (back):    PoolAdmin → setPoolAdminFees(pool, originalSpread, originalNotional)
Block N+1 (collect): Anyone → collectPoolFees(pool) → inflated fees sent to adminFeeDestination
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L44-45)
```text
  uint24 internal constant HARD_MAX_SPREAD_FEE_E6 = 200_000;
  uint24 internal constant HARD_MAX_NOTIONAL_FEE_E8 = 1_000_000;
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L408-434)
```text
  function setPoolAdminFees(address pool, uint24 newAdminSpreadFeeE6, uint24 newAdminNotionalFeeE8)
    external
    override
    nonReentrant
    onlyPoolAdmin(pool)
  {
    if (newAdminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    if (newAdminNotionalFeeE8 > maxAdminNotionalFeeE8) revert AdminFeeTooHigh();

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

    IMetricOmmPoolFactoryActions(pool)
      .setPoolFees(c.protocolSpreadFeeE6 + c.adminSpreadFeeE6, c.protocolNotionalFeeE8 + c.adminNotionalFeeE8);
    emit PoolAdminSpreadFeeUpdated(pool, newAdminSpreadFeeE6);
    emit PoolAdminNotionalFeeUpdated(pool, newAdminNotionalFeeE8);
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L450-457)
```text
  function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external
    override
    nonReentrant
    onlyPoolAdmin(pool)
  {
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
  }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L487-507)
```text
    uint256 executeAfter = block.timestamp + timelock;
    pendingPriceProvider[pool] = newPriceProvider;
    pendingPriceProviderExecuteAfter[pool] = executeAfter;
    emit PoolPriceProviderChangeProposed(pool, current, newPriceProvider, executeAfter);
  }

  /// @inheritdoc IMetricOmmPoolFactoryPoolAdmin
  function executePoolPriceProviderUpdate(address pool) external override nonReentrant onlyPoolAdmin(pool) {
    address pending = pendingPriceProvider[pool];
    if (pending == address(0)) revert NoPriceProviderChangeProposed();
    uint256 execAfter = pendingPriceProviderExecuteAfter[pool];
    // forge-lint: disable-next-line(block-timestamp) -- timelock enforcement legitimately relies on `block.timestamp`.
    if (block.timestamp < execAfter) revert PriceProviderTimelockNotElapsed(execAfter, block.timestamp);
    PoolImmutables memory p = IMetricOmmPool(pool).getImmutables();
    if (p.immutablePriceProvider != address(0)) revert PriceProviderImmutable();
    _validatePriceProvider(p.token0, p.token1, pending);
    IMetricOmmPoolFactoryActions(pool).setPriceProvider(pending);
    delete pendingPriceProvider[pool];
    delete pendingPriceProviderExecuteAfter[pool];
    emit PoolPriceProviderUpdated(pool, pending);
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L437-452)
```text
  function setPoolFees(uint24 newSpreadFeeE6, uint24 newNotionalFeeE8)
    external
    onlyFactory
    nonReentrant(PoolActions.SET_POOL_FEES)
  {
    unchecked {
      if (newSpreadFeeE6 != spreadFeeE6) {
        spreadFeeE6 = newSpreadFeeE6;
        emit SpreadFeeUpdated(newSpreadFeeE6);
      }
      if (newNotionalFeeE8 != notionalFeeE8) {
        notionalFeeE8 = newNotionalFeeE8;
        emit NotionalFeeUpdated(newNotionalFeeE8);
      }
    }
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L546-548)
```text
    uint256 nf = notionalFeeE8;
    buyPriceX64 = Math.mulDiv(askBeforeNotional, 1e8, 1e8 - nf, Math.Rounding.Ceil).toUint128();
    sellPriceX64 = Math.mulDiv(bidAfterSpread, 1e8 - nf, 1e8, Math.Rounding.Floor).toUint128();
```
