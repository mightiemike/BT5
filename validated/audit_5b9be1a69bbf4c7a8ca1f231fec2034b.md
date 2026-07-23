Audit Report

## Title
Pool Admin Bypasses `maxAdminSpreadFeeE6` Cap via `setPoolBinAdditionalFees` - (File: `metric-core/contracts/MetricOmmPoolFactory.sol`)

## Summary
`setPoolBinAdditionalFees` in `MetricOmmPoolFactory` forwards `addFeeBuyE6` and `addFeeSellE6` directly to the pool with no validation against `maxAdminSpreadFeeE6`. A pool admin can set per-bin additional fees up to `uint16` max (65,535 ≈ 6.55% in E6 scale) on any bin regardless of the factory-configured admin spread cap, bypassing the cap enforcement that `setPoolAdminFees` correctly enforces. These per-bin fees are applied additively to the global spread fee in every swap, causing traders to pay fees above the factory's intended ceiling.

## Finding Description
The factory enforces a layered fee-cap system. `HARD_MAX_SPREAD_FEE_E6 = 200_000` (20%) is the absolute ceiling for the global spread fee, and `maxAdminSpreadFeeE6` is a configurable sub-cap the factory owner can lower at any time. `setPoolAdminFees` correctly validates against this cap before writing:

```solidity
// MetricOmmPoolFactory.sol L414-415
if (newAdminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
if (newAdminNotionalFeeE8 > maxAdminNotionalFeeE8) revert AdminFeeTooHigh();
```

However, `setPoolBinAdditionalFees` performs no such validation:

```solidity
// MetricOmmPoolFactory.sol L450-457
function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external override nonReentrant onlyPoolAdmin(pool)
{
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
}
```

`setBinAdditionalFees` on the pool also performs no cap check, storing values directly:

```solidity
// MetricOmmPool.sol L464-474
function setBinAdditionalFees(int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external onlyFactory nonReentrant(PoolActions.SET_BIN_ADDITIONAL_FEES)
{
    BinState storage s = _binStates[bin];
    s.addFeeBuyE6 = addFeeBuyE6;
    s.addFeeSellE6 = addFeeSellE6;
}
```

These per-bin fees are applied additively to `baseFeeX64` (derived from the oracle spread) in every swap path:

```solidity
// MetricOmmPool.sol L910
params.baseFeeX64 + Math.mulDiv(binState.addFeeBuyE6, ONE_X64, 1e6)
// MetricOmmPool.sol L1088, L1177
params.baseFeeX64 + Math.mulDiv(binState.addFeeSellE6, ONE_X64, 1e6)
```

Exploit path:
1. Factory owner lowers `maxAdminSpreadFeeE6` to 0 to prevent admin fee abuse.
2. Pool admin calls `setPoolBinAdditionalFees(pool, bin, 65535, 65535)` — no revert, no validation.
3. `addFeeBuyE6 = 65535` (≈6.55%) is stored on-chain and applied to every swap through that bin.
4. `setPoolAdminFees` with the same effective amount would revert with `AdminFeeTooHigh`, confirming the bypass.

## Impact Explanation
Traders executing swaps through any bin where the pool admin has set `addFeeBuyE6`/`addFeeSellE6` to the `uint16` maximum pay up to 6.55% additional fee per bin, regardless of the factory owner's configured cap. This is a direct loss of trader principal on every affected swap. The factory's fee-cap system — the primary user-protection mechanism against admin fee abuse — is rendered ineffective for the per-bin fee dimension, constituting a broken admin-boundary invariant: pool admin exceeds caps.

## Likelihood Explanation
The pool admin is a semi-trusted role, and the contest scope explicitly lists "pool admin exceeds caps" as an in-scope admin-boundary break. The exploit requires a single transaction by the pool admin through the factory's `setPoolBinAdditionalFees` with no special preconditions, no timelock, and no co-signer. Any pool whose admin is malicious or compromised is immediately exploitable. The factory owner lowering `maxAdminSpreadFeeE6` to constrain the admin provides no protection against this path.

## Recommendation
Add a cap check inside `setPoolBinAdditionalFees` mirroring the validation already present in `setPoolAdminFees`:

```solidity
function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external override nonReentrant onlyPoolAdmin(pool)
{
    if (addFeeBuyE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    if (addFeeSellE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
}
```

Alternatively, introduce a dedicated `maxAdminBinAdditionalFeeE6` cap so the factory owner retains independent control over the ceiling for per-bin admin fees.

## Proof of Concept
```solidity
// 1. Factory owner lowers the admin spread cap to 0.
factory.setFeeCaps(200_000, 0, 1_000_000, 1_000_000);

// 2. Pool admin bypasses the cap via per-bin fees — no revert.
vm.prank(poolAdmin);
factory.setPoolBinAdditionalFees(pool, 0, 65535, 65535);
// addFeeBuyE6 = 65535 ≈ 6.55% — stored on-chain, no validation triggered.

// 3. Verify: setPoolAdminFees with same effective amount reverts.
vm.prank(poolAdmin);
vm.expectRevert(AdminFeeTooHigh.selector);
factory.setPoolAdminFees(pool, 65535, 0); // reverts — cap enforced here

// 4. Any trader swapping through bin 0 now pays ~6.55% additional fee
//    on top of the global spread fee, despite the factory cap being 0%.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L44-45)
```text
  uint24 internal constant HARD_MAX_SPREAD_FEE_E6 = 200_000;
  uint24 internal constant HARD_MAX_NOTIONAL_FEE_E8 = 1_000_000;
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L62-62)
```text
  uint24 public override maxAdminSpreadFeeE6;
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L414-415)
```text
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

**File:** metric-core/contracts/MetricOmmPool.sol (L906-914)
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
```

**File:** metric-core/contracts/types/PoolStorage.sol (L19-25)
```text
struct BinState {
  uint104 token0BalanceScaled;
  uint104 token1BalanceScaled;
  uint16 lengthE6;
  uint16 addFeeBuyE6;
  uint16 addFeeSellE6;
}
```
