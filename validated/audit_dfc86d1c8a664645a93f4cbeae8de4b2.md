Audit Report

## Title
Pool admin can set uncapped per-bin additional fees, bypassing the protocol's 20% hard fee ceiling — (`metric-core/contracts/MetricOmmPoolFactory.sol`)

## Summary
`setPoolBinAdditionalFees` in `MetricOmmPoolFactory.sol` forwards `addFeeBuyE6` and `addFeeSellE6` (both `uint16`, max 65,535 ≈ 6.55% in E6 units) directly to the pool with no upper-bound validation, while the equivalent global admin fee setter `setPoolAdminFees` enforces `maxAdminSpreadFeeE6`. During swaps, the per-bin fee is added as a separate addend on top of the oracle spread and global spread fee, allowing a pool admin to charge traders fees well above the protocol's intended 20% hard ceiling.

## Finding Description
The factory enforces a hard cap of `HARD_MAX_SPREAD_FEE_E6 = 200_000` (20%) on global spread fees and validates admin fees against `maxAdminSpreadFeeE6` in `setPoolAdminFees` (lines 414–415). However, `setPoolBinAdditionalFees` (lines 450–457) passes `addFeeBuyE6` and `addFeeSellE6` directly to `setBinAdditionalFees` on the pool with no cap check. The pool's `setBinAdditionalFees` (lines 464–474) also performs no upper-bound validation — it only checks the bin index range. During a swap, the per-bin fee is injected into swap math as `params.baseFeeX64 + Math.mulDiv(binState.addFeeBuyE6, ONE_X64, 1e6)` (line 910) and `params.baseFeeX64 + Math.mulDiv(binState.addFeeSellE6, ONE_X64, 1e6)` (line 1088), independent of and additive to the global `spreadFeeE6`. A pool admin can call `setPoolBinAdditionalFees(pool, bin, 65535, 65535)` in a single transaction with no timelock, setting per-bin fees to ~6.55% on top of the already-capped global spread fee.

## Impact Explanation
This is a direct admin-boundary break: the pool admin role is semi-trusted only within the caps the protocol explicitly enforces. The per-bin path is the only admin setter that skips governance. Traders swapping through an affected bin pay up to ~6.55% additional fee on top of the global spread fee (itself up to 20%), resulting in direct, quantifiable loss of user principal on every swap through that bin — well above the protocol's intended 20% hard ceiling.

## Likelihood Explanation
Any pool admin who discovers this gap can exploit it immediately with a single transaction (`setPoolBinAdditionalFees`) requiring no timelock, no protocol-owner approval, and no special preconditions beyond being the pool admin. The protocol's explicit design intent — demonstrated by the cap enforcement in `setPoolAdminFees` — is that no admin can charge more than `maxAdminSpreadFeeE6`; the per-bin path silently breaks this invariant.

## Recommendation
Add an upper-bound check in `setPoolBinAdditionalFees` against `maxAdminSpreadFeeE6` (or a dedicated constant), consistent with the cap structure applied to global admin fees:

```solidity
function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external override nonReentrant onlyPoolAdmin(pool)
{
    if (addFeeBuyE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    if (addFeeSellE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
}
```

Symmetrically, add the same check in `setBinAdditionalFees` on the pool as a defense-in-depth measure.

## Proof of Concept
1. Pool admin calls `factory.setPoolBinAdditionalFees(pool, 0, 65535, 65535)` — no revert, as `setPoolBinAdditionalFees` (line 456) passes values directly with no cap check.
2. The pool stores `addFeeBuyE6 = 65535` and `addFeeSellE6 = 65535` on bin 0 via `setBinAdditionalFees` (lines 471–472), which also has no upper-bound validation.
3. A trader calls `pool.swap(...)` routing through bin 0. The swap math computes (line 910):
   ```
   params.baseFeeX64 + Math.mulDiv(65535, ONE_X64, 1e6)
   ```
   adding ~6.55% on top of the oracle spread and the global spread fee (up to 20%).
4. The trader pays ~6.55% more than the protocol's hard cap permits, with no on-chain guard preventing it.