### Title
Pool Admin Can Bypass Factory Fee Caps via Uncapped Bin Additional Fees - (File: `metric-core/contracts/MetricOmmPoolFactory.sol`)

### Summary

The factory enforces explicit per-pool caps on admin spread and notional fees via `maxAdminSpreadFeeE6` / `maxAdminNotionalFeeE8`. However, `setPoolBinAdditionalFees` applies no analogous cap, allowing a pool admin to set per-bin `addFeeBuyE6` / `addFeeSellE6` up to the full `uint16` maximum (65 535 = 6.5535% in E6 scale) regardless of what the factory owner has configured as the admin fee ceiling.

### Finding Description

`MetricOmmPoolFactory.setPoolAdminFees` correctly validates that the new admin fees do not exceed the factory-governed caps:

```solidity
if (newAdminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
if (newAdminNotionalFeeE8 > maxAdminNotionalFeeE8) revert AdminFeeTooHigh();
``` [1](#0-0) 

`setPoolBinAdditionalFees`, by contrast, forwards the caller-supplied values directly to the pool with zero validation:

```solidity
function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external override nonReentrant onlyPoolAdmin(pool)
{
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
}
``` [2](#0-1) 

The factory owner can lower `maxAdminSpreadFeeE6` to any value (e.g., 1 000 = 0.1%) to protect swappers, but a pool admin can immediately call `setPoolBinAdditionalFees` with `addFeeBuyE6 = 65535` (6.5535%) on any bin, completely circumventing that governance decision. The hard-coded ceiling for admin spread fees is `HARD_MAX_SPREAD_FEE_E6 = 200_000` (20%): [3](#0-2) 

The bin additional fees are stored in `BinState.addFeeBuyE6` / `addFeeSellE6` as `uint16` fields and are applied on top of the spread fee during every swap through that bin: [4](#0-3) 

### Impact Explanation

A pool admin (semi-trusted, analogous to a market maker) can charge swappers up to 6.5535% extra per bin on top of the oracle spread fee, bypassing the factory's fee governance. This is a direct loss of swapper principal: the excess fee is extracted from the swap input amount and credited to the pool (or admin fee destination) beyond what the factory owner intended to permit. The factory's cap system — the primary protection against pool admin overcharging — is rendered ineffective for bin-level fees.

### Likelihood Explanation

Any pool admin can trigger this immediately after pool creation with a single transaction. No special conditions, oracle manipulation, or external state is required. The factory owner's ability to lower `maxAdminSpreadFeeE6` provides no protection because `setPoolBinAdditionalFees` is entirely outside that enforcement path.

### Recommendation

Add a cap check inside `setPoolBinAdditionalFees` mirroring the check in `setPoolAdminFees`:

```solidity
function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external override nonReentrant onlyPoolAdmin(pool)
{
    if (addFeeBuyE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    if (addFeeSellE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
}
```

Alternatively, introduce a dedicated `maxAdminBinAdditionalFeeE6` cap that the factory owner can configure independently.

### Proof of Concept

1. Factory owner sets `maxAdminSpreadFeeE6 = 1_000` (0.1%) via `setFeeCaps` to protect swappers.
2. Pool admin calls `setPoolBinAdditionalFees(pool, 0, 65535, 65535)`.
3. No revert occurs — `addFeeBuyE6 = 65535` (6.5535%) is written directly to `BinState` for bin 0.
4. The next swapper routing through bin 0 pays 6.5535% in bin additional fees on top of the oracle spread, far exceeding the 0.1% cap the factory owner intended to enforce.
5. The excess fee accrues to the pool and is collectible by the pool admin via `collectPoolFees`, constituting a direct loss of swapper principal beyond the factory-governed limit. [5](#0-4) [6](#0-5) [2](#0-1)

### Citations

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L44-45)
```text
  uint24 internal constant HARD_MAX_SPREAD_FEE_E6 = 200_000;
  uint24 internal constant HARD_MAX_NOTIONAL_FEE_E8 = 1_000_000;
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L284-315)
```text
  function setFeeCaps(
    uint24 newMaxProtocolSpreadFeeE6,
    uint24 newMaxAdminSpreadFeeE6,
    uint24 newMaxProtocolNotionalFeeE8,
    uint24 newMaxAdminNotionalFeeE8
  ) external override onlyOwner {
    if (
      newMaxProtocolSpreadFeeE6 > HARD_MAX_SPREAD_FEE_E6 || newMaxAdminSpreadFeeE6 > HARD_MAX_SPREAD_FEE_E6
        || newMaxProtocolNotionalFeeE8 > HARD_MAX_NOTIONAL_FEE_E8 || newMaxAdminNotionalFeeE8 > HARD_MAX_NOTIONAL_FEE_E8
    ) {
      revert FeeCapsExceedHardLimit();
    }
    maxProtocolSpreadFeeE6 = newMaxProtocolSpreadFeeE6;
    maxAdminSpreadFeeE6 = newMaxAdminSpreadFeeE6;
    maxProtocolNotionalFeeE8 = newMaxProtocolNotionalFeeE8;
    maxAdminNotionalFeeE8 = newMaxAdminNotionalFeeE8;

    if (spreadProtocolFeeE6 > newMaxProtocolSpreadFeeE6) {
      uint24 oldFeeE6 = spreadProtocolFeeE6;
      spreadProtocolFeeE6 = newMaxProtocolSpreadFeeE6;
      emit SpreadProtocolFeeDefaultUpdated(oldFeeE6, newMaxProtocolSpreadFeeE6);
    }
    if (protocolNotionalFeeE8 > newMaxProtocolNotionalFeeE8) {
      uint24 oldFeeE8 = protocolNotionalFeeE8;
      protocolNotionalFeeE8 = newMaxProtocolNotionalFeeE8;
      emit ProtocolNotionalFeeDefaultUpdated(oldFeeE8, newMaxProtocolNotionalFeeE8);
    }

    emit FeeCapsUpdated(
      newMaxProtocolSpreadFeeE6, newMaxAdminSpreadFeeE6, newMaxProtocolNotionalFeeE8, newMaxAdminNotionalFeeE8
    );
  }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L408-435)
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
  }
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

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L626-630)
```text
          (uint16 length, uint16 buyFee, uint16 sellFee) = binData.unpack();
          if (length == 0) break;
          nonNegativeBinStates[k] = BinState({
            token0BalanceScaled: 0, token1BalanceScaled: 0, lengthE6: length, addFeeBuyE6: buyFee, addFeeSellE6: sellFee
          });
```
