### Title
Pool Admin Bypasses Factory Fee Caps via Uncapped `setPoolBinAdditionalFees` — (File: metric-core/contracts/MetricOmmPoolFactory.sol)

### Summary

The factory's fee governance system enforces per-admin caps (`maxAdminSpreadFeeE6`) on global spread fees, but `setPoolBinAdditionalFees` passes bin-level additional fees directly to the pool with **no cap check**, allowing the pool admin to impose per-bin fees up to `uint16.max = 65535` (6.5535% in E6 units) regardless of what the factory owner has configured as the admin fee ceiling.

### Finding Description

`setPoolAdminFees` correctly enforces the factory's configured cap:

```solidity
// MetricOmmPoolFactory.sol L414-415
if (newAdminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
if (newAdminNotionalFeeE8 > maxAdminNotionalFeeE8) revert AdminFeeTooHigh();
``` [1](#0-0) 

But `setPoolBinAdditionalFees` performs **no cap validation** and forwards the raw `uint16` values directly to the pool:

```solidity
// MetricOmmPoolFactory.sol L450-457
function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external override nonReentrant onlyPoolAdmin(pool)
{
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
}
``` [2](#0-1) 

The pool's `setBinAdditionalFees` also performs no cap check — it only validates the bin index: [3](#0-2) 

The stored `addFeeBuyE6` / `addFeeSellE6` are then added directly to the base fee on every swap through that bin:

```solidity
params.baseFeeX64 + Math.mulDiv(binState.addFeeBuyE6, ONE_X64, 1e6),
``` [4](#0-3) [5](#0-4) 

The hard cap constants are `HARD_MAX_SPREAD_FEE_E6 = 200_000` (20%) and `HARD_MAX_NOTIONAL_FEE_E8 = 1_000_000` (1%): [6](#0-5) 

`uint16.max = 65535` in E6 units equals **6.5535%** additional fee per bin — unchecked against any factory-configured cap. The factory owner can lower `maxAdminSpreadFeeE6` to, say, 1% (`10_000`) to protect traders, but the pool admin can still call `setPoolBinAdditionalFees(pool, bin, 65535, 65535)` and impose 6.5535% additional fee on every swap through that bin, completely bypassing the owner's governance intent.

There is also **no timelock** on this setter, unlike the oracle rotation path which requires `priceProviderTimelock`. The change takes effect immediately on the next swap.

The higher effective spread increases the pool's surplus. When `collectFees` is called, the admin receives `adminSpreadFeeE6 / (adminSpreadFeeE6 + protocolSpreadFeeE6)` of that surplus — so the admin indirectly extracts a share of the extra fees imposed on traders: [7](#0-6) 

### Impact Explanation

Traders swapping through affected bins pay up to 6.5535% more than the factory's configured admin fee cap permits. The pool admin receives a proportional share of the inflated surplus through `collectFees`, constituting a direct financial loss for traders that bypasses the factory owner's fee governance. This is an admin-boundary break: the factory owner's `maxAdminSpreadFeeE6` cap is rendered ineffective for per-bin fees.

### Likelihood Explanation

The pool admin is a semi-trusted role bounded by factory caps. The bypass requires only a single call to `setPoolBinAdditionalFees` with `addFeeBuyE6 = type(uint16).max`. No special conditions, no timelock, no oracle state required. Any pool admin can trigger this at any time on any active pool.

### Recommendation

Add a cap check in `setPoolBinAdditionalFees` analogous to `setPoolAdminFees`:

```solidity
function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external override nonReentrant onlyPoolAdmin(pool)
{
    if (addFeeBuyE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    if (addFeeSellE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
}
```

Alternatively, introduce a dedicated `maxBinAdditionalFeeE6` cap that the factory owner can configure independently.

### Proof of Concept

1. Factory owner sets `maxAdminSpreadFeeE6 = 10_000` (1%) via `setFeeCaps`.
2. Pool admin attempts `setPoolAdminFees(pool, 10_001, 0)` → reverts with `AdminFeeTooHigh`. Cap is enforced.
3. Pool admin calls `setPoolBinAdditionalFees(pool, 0, 65535, 65535)` → **succeeds**. Bin 0 now carries 6.5535% additional buy and sell fee.
4. Next trader swapping through bin 0 pays `baseFeeX64 + mulDiv(65535, ONE_X64, 1e6)` — 6.5535% above the oracle price, far exceeding the factory owner's intended 1% ceiling.
5. On the next `collectPoolFees` call, the admin receives their proportional share of the inflated surplus, extracting value from traders beyond what the factory's cap system was designed to permit. [8](#0-7) [2](#0-1) [3](#0-2)

### Citations

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L43-45)
```text
  /// @dev Owner `setFeeCaps` values cannot exceed these (spread: 1e6 = 100%; notional: 1e8 = 100%)
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

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L408-415)
```text
  function setPoolAdminFees(address pool, uint24 newAdminSpreadFeeE6, uint24 newAdminNotionalFeeE8)
    external
    override
    nonReentrant
    onlyPoolAdmin(pool)
  {
    if (newAdminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    if (newAdminNotionalFeeE8 > maxAdminNotionalFeeE8) revert AdminFeeTooHigh();
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

**File:** metric-core/contracts/MetricOmmPool.sol (L391-395)
```text
      uint256 spreadFee0ToAdminScaled = spreadSumE6 == 0 ? 0 : (surplus0Scaled * adminSpreadFeeE6_) / spreadSumE6;
      uint256 spreadFee1ToAdminScaled = spreadSumE6 == 0 ? 0 : (surplus1Scaled * adminSpreadFeeE6_) / spreadSumE6;

      uint256 spreadFee0ToProtocolScaled = spreadSumE6 == 0 ? 0 : (surplus0Scaled * protocolSpreadFeeE6_) / spreadSumE6;
      uint256 spreadFee1ToProtocolScaled = spreadSumE6 == 0 ? 0 : (surplus1Scaled * protocolSpreadFeeE6_) / spreadSumE6;
```

**File:** metric-core/contracts/MetricOmmPool.sol (L464-474)
```text
  function setBinAdditionalFees(int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external
    onlyFactory
    nonReentrant(PoolActions.SET_BIN_ADDITIONAL_FEES)
  {
    if (bin < LOWEST_BIN || bin > HIGHEST_BIN) revert InvalidBinIndex(bin);
    BinState storage s = _binStates[bin];
    s.addFeeBuyE6 = addFeeBuyE6;
    s.addFeeSellE6 = addFeeSellE6;
    emit BinAdditionalFeesUpdated(bin, addFeeBuyE6, addFeeSellE6);
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L906-915)
```text
          (curPosInBinCache, delta0Scaled, delta1Scaled, binLpFeeAmountScaled) = SwapMath.buyToken0InBinSpecifiedOut(
            binState,
            curPosInBinCache,
            state,
            params.baseFeeX64 + Math.mulDiv(binState.addFeeBuyE6, ONE_X64, 1e6),
            lowerPriceX64,
            upperPriceX64,
            params.priceLimitX64,
            spreadFeeE6
          );
```

**File:** metric-core/contracts/MetricOmmPool.sol (L994-1004)
```text
          (curPosInBinCache, outToken0AmountScaled, delta0Scaled, delta1Scaled, binLpFeeAmountScaled) =
            SwapMath.buyToken0InBinSpecifiedIn(
              binState,
              curPosInBinCache,
              state,
              params.baseFeeX64 + Math.mulDiv(binState.addFeeBuyE6, ONE_X64, 1e6),
              lowerPriceX64,
              upperPriceX64,
              params.priceLimitX64,
              spreadFeeE6
            );
```
