### Title
Pool Admin Bypasses Fee Cap via Uncapped Per-Bin Additional Fees in `setPoolBinAdditionalFees` — (File: `metric-core/contracts/MetricOmmPoolFactory.sol`)

---

### Summary

`setPoolBinAdditionalFees` allows the pool admin to set per-bin additional spread fees (`addFeeBuyE6`, `addFeeSellE6`) with no upper-bound validation, while the base admin fee setter `setPoolAdminFees` enforces `maxAdminSpreadFeeE6`. A pool admin can silently exceed the protocol-enforced fee cap on targeted bins, causing traders to pay more than the maximum the cap system is designed to allow.

---

### Finding Description

The factory enforces a fee cap in `setPoolAdminFees`:

```solidity
if (newAdminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
``` [1](#0-0) 

However, `setPoolBinAdditionalFees` passes `addFeeBuyE6` and `addFeeSellE6` directly to the pool with **no cap check**:

```solidity
function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external override nonReentrant onlyPoolAdmin(pool)
{
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
}
``` [2](#0-1) 

The pool's `setBinAdditionalFees` only validates the bin index, not the fee magnitude:

```solidity
if (bin < LOWEST_BIN || bin > HIGHEST_BIN) revert InvalidBinIndex(bin);
BinState storage s = _binStates[bin];
s.addFeeBuyE6 = addFeeBuyE6;
s.addFeeSellE6 = addFeeSellE6;
``` [3](#0-2) 

During swap execution, the effective fee applied to a bin is:

```solidity
uint256 buyFeeX64 = baseFeeX64 + Math.mulDiv(binState.addFeeBuyE6, ONE_X64, 1e6);
``` [4](#0-3) 

The additional fee is added **on top of** the base spread fee. The `BinState` struct stores `addFeeBuyE6` and `addFeeSellE6` as `uint16`, allowing values up to 65 535 (≈ 6.55 % in E6 units):

```solidity
struct BinState {
  uint104 token0BalanceScaled;
  uint104 token1BalanceScaled;
  uint16 lengthE6;
  uint16 addFeeBuyE6;   // no cap enforced post-creation
  uint16 addFeeSellE6;  // no cap enforced post-creation
}
``` [5](#0-4) 

The hard cap on the base spread fee is `HARD_MAX_SPREAD_FEE_E6 = 200 000` (20 %) per component, so the maximum capped total base fee is 40 %. Adding `addFeeBuyE6 = 65 535` raises the effective fee for that bin to ≈ 46.55 %, well above the cap the factory owner intended to enforce.

The resulting inflated ask/bid prices are computed as:

```solidity
uint256 askBeforeNotional = Math.mulDiv(marginalPriceX64, ONE_X64 + buyFeeX64, ONE_X64, Math.Rounding.Ceil);
uint256 bidAfterSpread    = Math.mulDiv(marginalPriceX64, ONE_X64, ONE_X64 + sellFeeX64, Math.Rounding.Floor);
``` [6](#0-5) 

The excess spread accrues as surplus and is collected by the admin via `collectFees`. [7](#0-6) 

---

### Impact Explanation

Traders swapping through the targeted bin pay fees above the protocol-enforced cap. The excess is extracted as spread surplus during `collectFees`, flowing to the admin fee destination. This is a direct, quantifiable loss of trader principal — up to 6.55 % per swap on the affected bin — that the fee cap system was explicitly designed to prevent.

---

### Likelihood Explanation

The pool admin is a semi-trusted role. The fee cap system (`maxAdminSpreadFeeE6`, `HARD_MAX_SPREAD_FEE_E6`) exists precisely because the pool admin is not fully trusted. A malicious or compromised pool admin can call `setPoolBinAdditabilityFees` with `addFeeBuyE6 = 65535` at any time with no timelock, no additional privilege, and no on-chain signal distinguishing it from a legitimate fine-tuning call. The bypass requires a single transaction.

---

### Recommendation

Add a cap check in `setPoolBinAdditionalFees` (and propagate it through `setBinAdditionalFees`) consistent with the cap enforced in `setPoolAdminFees`:

```solidity
// In MetricOmmPoolFactory.setPoolBinAdditionalFees:
if (addFeeBuyE6  > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
if (addFeeSellE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
```

Alternatively, enforce the cap inside `MetricOmmPool.setBinAdditionalFees` so the pool itself rejects out-of-range values regardless of the caller path.

---

### Proof of Concept

1. Factory owner sets `maxAdminSpreadFeeE6 = 200 000` (20 %) via `setFeeCaps`.
2. Pool admin calls `setPoolAdminFees(pool, 200_000, 0)` — accepted, at the cap.
3. Pool admin calls `setPoolBinAdditionalFees(pool, 0, 65535, 65535)` — **no revert**, accepted.
4. A trader swaps through bin 0. The effective buy fee is `200 000 + 65 535 = 265 535` (26.55 %) instead of the capped 20 %, and the effective sell fee is identical.
5. The inflated ask price causes the trader to send ≈ 6.55 % more token1 than the cap permits; the excess accrues as spread surplus.
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

**File:** metric-core/contracts/MetricOmmPool.sol (L385-388)
```text
    uint256 surplus0Scaled =
      balance0() * TOKEN_0_SCALE_MULTIPLIER - uint256(binTotals.scaledToken0) - notionalFee0AmountScaled;
    uint256 surplus1Scaled =
      balance1() * TOKEN_1_SCALE_MULTIPLIER - uint256(binTotals.scaledToken1) - notionalFee1AmountScaled;
```

**File:** metric-core/contracts/MetricOmmPool.sol (L469-473)
```text
    if (bin < LOWEST_BIN || bin > HIGHEST_BIN) revert InvalidBinIndex(bin);
    BinState storage s = _binStates[bin];
    s.addFeeBuyE6 = addFeeBuyE6;
    s.addFeeSellE6 = addFeeSellE6;
    emit BinAdditionalFeesUpdated(bin, addFeeBuyE6, addFeeSellE6);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L543-544)
```text
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
