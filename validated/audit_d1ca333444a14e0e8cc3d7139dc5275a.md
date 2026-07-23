### Title
Per-bin additional fees (`addFeeBuyE6`/`addFeeSellE6`) have no upper bound, allowing pool admin to bypass the protocol's hard fee cap — (`metric-core/contracts/MetricOmmPoolFactory.sol`)

---

### Summary

`setPoolBinAdditionalFees` passes `addFeeBuyE6` and `addFeeSellE6` (both `uint16`, max 65,535 ≈ 6.55% in E6 units) directly to the pool with no upper-bound validation. The global spread fee is capped at a hard maximum of 20% (`HARD_MAX_SPREAD_FEE_E6 = 200_000`), but per-bin additional fees are injected into swap math as a separate addend on top of the oracle spread, completely bypassing that cap. A pool admin can set per-bin fees to `type(uint16).max` on any bin, causing traders to pay fees well above the protocol's intended ceiling.

---

### Finding Description

The factory enforces a two-layer fee cap for the global spread fee:

1. `HARD_MAX_SPREAD_FEE_E6 = 200_000` (20%) — absolute ceiling for `setFeeCaps`.
2. `maxAdminSpreadFeeE6` — per-pool cap enforced in `setPoolAdminFees`. [1](#0-0) [2](#0-1) 

However, `setPoolBinAdditionalFees` — callable by the pool admin — passes `addFeeBuyE6` and `addFeeSellE6` straight through to `setBinAdditionalFees` on the pool with **no cap check at all**: [3](#0-2) 

The pool's `setBinAdditionalFees` also performs no upper-bound validation: [4](#0-3) 

During a swap, the per-bin fee is added to `baseFeeX64` (the oracle spread component) as a separate addend, independent of the global `spreadFeeE6`: [5](#0-4) [6](#0-5) 

Because `addFeeBuyE6`/`addFeeSellE6` are `uint16`, the maximum settable per-bin fee is 65,535 E6 units ≈ **6.55%** per bin. This is added on top of both the oracle spread and the global spread fee (which itself can be up to 20%), making the effective fee for swaps in that bin up to **~26.5%** — far above the protocol's intended 20% hard ceiling.

The bin packing format at pool creation also encodes `addFeeBuyE6`/`addFeeSellE6` as `uint16` fields with no factory-side cap validation: [7](#0-6) 

---

### Impact Explanation

Traders swapping through a bin where the pool admin has set `addFeeBuyE6` or `addFeeSellE6` to `type(uint16).max` (65,535) pay an additional ~6.55% fee on top of the already-capped global spread fee. This is a direct, quantifiable loss of user principal on every swap through that bin. The protocol's explicit intent — enforced for global fees — is that no admin can charge more than 20% spread; per-bin fees break this invariant silently.

---

### Likelihood Explanation

The pool admin is a semi-trusted role. The protocol explicitly caps the admin's global spread fee at `maxAdminSpreadFeeE6` (≤ 20%), demonstrating that the design intent is to bound what the admin can extract. The per-bin path is the only setter that skips this governance. Any pool admin who discovers this gap can exploit it immediately with a single transaction, with no timelock or protocol-owner approval required.

---

### Recommendation

Add an upper-bound check in `setPoolBinAdditionalFees` (and symmetrically in `setBinAdditionalFees` on the pool) against a configurable or hard-coded cap. For example, enforce that `addFeeBuyE6` and `addFeeSellE6` each do not exceed `maxAdminSpreadFeeE6` (or a dedicated `maxBinAdditionalFeeE6` constant), consistent with the cap structure applied to global admin fees:

```solidity
function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external override nonReentrant onlyPoolAdmin(pool)
{
    if (addFeeBuyE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    if (addFeeSellE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
}
```

---

### Proof of Concept

1. Pool admin calls `factory.setPoolBinAdditionalFees(pool, 0, 65535, 65535)` — no revert.
2. The pool stores `addFeeBuyE6 = 65535` and `addFeeSellE6 = 65535` on bin 0.
3. A trader calls `pool.swap(...)` routing through bin 0. The swap math computes:
   ```
   totalBuyFeeX64 = baseFeeX64 + mulDiv(65535, ONE_X64, 1e6)
   ```
   adding ~6.55% on top of the oracle spread and the global 20% spread fee.
4. The trader pays ~6.55% more than the protocol's hard cap permits, with no on-chain guard preventing it. [3](#0-2) [5](#0-4)

### Citations

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L44-45)
```text
  uint24 internal constant HARD_MAX_SPREAD_FEE_E6 = 200_000;
  uint24 internal constant HARD_MAX_NOTIONAL_FEE_E8 = 1_000_000;
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

**File:** metric-core/contracts/MetricOmmPool.sol (L1084-1093)
```text
          (curPosInBinCache, delta0Scaled, delta1Scaled, binLpFeeAmountScaled) = SwapMath.buyToken1InBinSpecifiedOut(
            binState,
            curPosInBinCache,
            state,
            params.baseFeeX64 + Math.mulDiv(binState.addFeeSellE6, ONE_X64, 1e6),
            lowerPriceX64,
            upperPriceX64,
            params.priceLimitX64,
            spreadFeeE6
          );
```

**File:** metric-core/docs/POOL_CONFIGURATION_AND_MANAGEMENT.md (L88-93)
```markdown
Each logical bin is **48 bits**:

- **bits 0–15:** `lengthE6` (uint16) — segment length in E6 distance units along the ladder.
- **bits 16–31:** `addFeeBuyE6` (uint16) — extra fee for the “buy token0” direction (E6).
- **bits 32–47:** `addFeeSellE6` (uint16) — extra fee for the “buy token1” direction (E6).

```
