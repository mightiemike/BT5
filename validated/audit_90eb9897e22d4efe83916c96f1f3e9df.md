Audit Report

## Title
Pool Admin Bypasses Fee Cap via Uncapped Per-Bin Additional Fees in `setPoolBinAdditionalFees` — (File: `metric-core/contracts/MetricOmmPoolFactory.sol`)

## Summary

`setPoolBinAdditionalFees` allows the pool admin to set per-bin additional spread fees (`addFeeBuyE6`, `addFeeSellE6`) with no upper-bound validation, while `setPoolAdminFees` enforces `maxAdminSpreadFeeE6`. A pool admin can silently exceed the protocol-enforced fee cap on targeted bins, causing traders to pay more than the maximum the cap system is designed to allow, with the excess accruing as spread surplus collectible by the admin.

## Finding Description

`setPoolAdminFees` enforces the fee cap at [1](#0-0) :

```solidity
if (newAdminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
```

However, `setPoolBinAdditionalFees` passes `addFeeBuyE6` and `addFeeSellE6` directly to the pool with no cap check: [2](#0-1) 

The pool's `setBinAdditionalFees` only validates the bin index, not the fee magnitude: [3](#0-2) 

`BinState` stores `addFeeBuyE6` and `addFeeSellE6` as `uint16`, allowing values up to 65,535 (~6.55% in E6 units): [4](#0-3) 

During swap execution, the additional fee is added on top of the base spread fee: [5](#0-4) 

The resulting inflated ask/bid prices are computed as: [6](#0-5) 

The hard cap `HARD_MAX_SPREAD_FEE_E6 = 200_000` (20%) is defined at the factory level: [7](#0-6) 

Setting `addFeeBuyE6 = 65535` raises the effective fee for a targeted bin to ~26.55%, well above the 20% cap the factory owner intended to enforce.

## Impact Explanation

Traders swapping through the targeted bin pay fees above the protocol-enforced cap. The excess accrues as spread surplus and is collected by the admin via `collectFees`. This is a direct, quantifiable loss of trader principal — up to ~6.55% per swap on the affected bin — that the fee cap system was explicitly designed to prevent. This constitutes an admin-boundary break: pool admin exceeds caps the protocol enforces.

## Likelihood Explanation

The pool admin is semi-trusted only inside caps and timelocks. The fee cap system (`maxAdminSpreadFeeE6`, `HARD_MAX_SPREAD_FEE_E6`) exists precisely because the pool admin is not fully trusted. A malicious or compromised pool admin can call `setPoolBinAdditionalFees` with `addFeeBuyE6 = 65535` at any time with no timelock, no additional privilege, and no on-chain signal distinguishing it from a legitimate fine-tuning call. The bypass requires a single transaction.

## Recommendation

Add a cap check in `setPoolBinAdditionalFees` consistent with the cap enforced in `setPoolAdminFees`:

```solidity
// In MetricOmmPoolFactory.setPoolBinAdditionalFees:
if (addFeeBuyE6  > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
if (addFeeSellE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
```

Alternatively, enforce the cap inside `MetricOmmPool.setBinAdditionalFees` so the pool itself rejects out-of-range values regardless of the caller path.

## Proof of Concept

1. Factory owner sets `maxAdminSpreadFeeE6 = 200_000` (20%) via `setFeeCaps`.
2. Pool admin calls `setPoolAdminFees(pool, 200_000, 0)` — accepted, at the cap.
3. Pool admin calls `setPoolBinAdditionalFees(pool, 0, 65535, 65535)` — **no revert**, accepted.
4. A trader swaps through bin 0. The effective buy fee is `200_000 + 65_535 = 265_535` (~26.55%) instead of the capped 20%, and the effective sell fee is identical.
5. The inflated ask price causes the trader to send ~6.55% more token1 than the cap permits; the excess accrues as spread surplus.
6. Pool admin calls `collectPoolFees` and receives the excess above the intended cap.

### Citations

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L44-44)
```text
  uint24 internal constant HARD_MAX_SPREAD_FEE_E6 = 200_000;
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

**File:** metric-core/contracts/MetricOmmPool.sol (L469-473)
```text
    if (bin < LOWEST_BIN || bin > HIGHEST_BIN) revert InvalidBinIndex(bin);
    BinState storage s = _binStates[bin];
    s.addFeeBuyE6 = addFeeBuyE6;
    s.addFeeSellE6 = addFeeSellE6;
    emit BinAdditionalFeesUpdated(bin, addFeeBuyE6, addFeeSellE6);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L540-544)
```text
    uint256 buyFeeX64 = baseFeeX64 + Math.mulDiv(binState.addFeeBuyE6, ONE_X64, 1e6);
    uint256 sellFeeX64 = baseFeeX64 + Math.mulDiv(binState.addFeeSellE6, ONE_X64, 1e6);

    uint256 askBeforeNotional = Math.mulDiv(marginalPriceX64, ONE_X64 + buyFeeX64, ONE_X64, Math.Rounding.Ceil);
    uint256 bidAfterSpread = Math.mulDiv(marginalPriceX64, ONE_X64, ONE_X64 + sellFeeX64, Math.Rounding.Floor);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L999-999)
```text
              params.baseFeeX64 + Math.mulDiv(binState.addFeeBuyE6, ONE_X64, 1e6),
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
