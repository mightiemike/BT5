Audit Report

## Title
Pool Admin Bypasses Fee Cap via Uncapped Per-Bin Additional Fees in `setPoolBinAdditionalFees` — (File: `metric-core/contracts/MetricOmmPoolFactory.sol`)

## Summary

`setPoolBinAdditionalFees` forwards `addFeeBuyE6` and `addFeeSellE6` directly to `MetricOmmPool.setBinAdditionalFees` with no upper-bound validation, while `setPoolAdminFees` enforces `maxAdminSpreadFeeE6`. A pool admin can set per-bin additional fees up to `uint16` max (65 535, ≈6.55 % in E6 units) on any bin, silently exceeding the protocol-enforced fee cap and extracting the excess from traders as spread surplus.

## Finding Description

`setPoolAdminFees` enforces the cap at L414–415: [1](#0-0) 

`setPoolBinAdditionalFees` at L450–457 performs no equivalent check and passes values straight through: [2](#0-1) 

`setBinAdditionalFees` in the pool validates only the bin index, not the fee magnitude: [3](#0-2) 

`BinState` stores `addFeeBuyE6`/`addFeeSellE6` as `uint16`, accepting values up to 65 535: [4](#0-3) 

During swap execution the additional fee is added on top of `baseFeeX64`: [5](#0-4) 

The inflated fee directly raises the ask/bid prices returned to traders: [6](#0-5) 

## Impact Explanation

This is a direct admin-boundary break: the pool admin, a semi-trusted role constrained by `maxAdminSpreadFeeE6`, can exceed that cap on any individual bin. Traders swapping through the targeted bin pay up to ≈6.55 % more than the cap permits; the excess accrues as spread surplus and is collected by the admin via `collectFees`. This constitutes a direct, quantifiable loss of trader principal that the fee cap system was explicitly designed to prevent.

## Likelihood Explanation

The pool admin requires no additional privilege beyond their existing role. The call is a single transaction with no timelock, no on-chain signal distinguishing it from a legitimate fine-tuning call, and no guard anywhere in the call path. The fee cap system (`maxAdminSpreadFeeE6`, `HARD_MAX_SPREAD_FEE_E6`) exists precisely because the pool admin is not fully trusted, making this bypass directly contrary to the protocol's trust model.

## Recommendation

Add cap checks in `setPoolBinAdditionalFees` before forwarding to the pool:

```solidity
if (addFeeBuyE6  > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
if (addFeeSellE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
```

Alternatively, enforce the cap inside `MetricOmmPool.setBinAdditionalFees` so the pool itself rejects out-of-range values regardless of the caller path.

## Proof of Concept

1. Factory owner sets `maxAdminSpreadFeeE6 = 200_000` (20 %) via `setFeeCaps`.
2. Pool admin calls `setPoolAdminFees(pool, 200_000, 0)` — accepted, at the cap.
3. Pool admin calls `setPoolBinAdditionalFees(pool, 0, 65535, 65535)` — no revert, accepted.
4. A trader swaps through bin 0. Effective buy fee = `200_000 + 65_535 = 265_535` (≈26.55 %) instead of the capped 20 %.
5. The inflated ask price causes the trader to send ≈6.55 % more token1 than the cap permits; the excess accrues as spread surplus.
6. Pool admin calls `collectPoolFees` and receives the excess above the intended cap.

### Citations

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
