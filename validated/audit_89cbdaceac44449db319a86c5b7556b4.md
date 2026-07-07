### Title
Global `lastLiquidationFees` Cross-Subaccount Contamination Causes Premature Socialization — (`core/contracts/ClearinghouseLiq.sol`)

---

### Summary

`lastLiquidationFees` is a single global `int128` in `ClearinghouseStorage` with no per-subaccount scoping. After liquidating subaccount A (which writes `lastLiquidationFees = X`), a finalization call for a different subaccount B reads the same stale value, computing an artificially deflated `v.insurance`. This causes `v.canLiquidateMore` to be `false` when it should be `true`, skipping the spot-liability-zero guard and triggering `spotEngine.socializeSubaccount` even when the insurance fund had sufficient balance to cover B's deficit without socialization.

---

### Finding Description

`lastLiquidationFees` is declared as a single global slot: [1](#0-0) 

It is written unconditionally at the end of every non-finalization liquidation step, regardless of which subaccount was liquidated: [2](#0-1) 

The design intent (per the inline comment) is to prevent a race where the last liquidation step of the **same** subaccount refills insurance just enough to block socialization. The value is meant to represent the fees just added for the subaccount currently being finalized.

In `_finalizeSubaccount`, the variable is consumed without any check that it belongs to the subaccount being finalized: [3](#0-2) 

`v.canLiquidateMore` gates the only check that requires spot liabilities to be exactly zero: [4](#0-3) 

The earlier spot check (lines 300–311) only requires `balance.amount <= 0`, so negative spot balances (liabilities) pass it silently: [5](#0-4) 

When `v.canLiquidateMore` is incorrectly `false`, execution falls through to `perpEngine.socializeSubaccount` and then `spotEngine.socializeSubaccount` with an artificially depleted `v.insurance`: [6](#0-5) 

Note that `v.insurance += lastLiquidationFees` at line 410 restores the correct value before writing back to storage, so the **stored** `insurance` is ultimately correct — but the intermediate operations (perp socialization, spot insurance cover, spot socialization decision) have already executed against the wrong value.

---

### Impact Explanation

**Premature bad-debt socialization.** When `insurance > 0` but `insurance - lastLiquidationFees(A) ≤ 0`:

- `v.canLiquidateMore = false` → spot-liability-zero check skipped → subaccount B with non-zero spot liabilities is finalized.
- `perpEngine.socializeSubaccount(B, v.insurance)` receives a deflated or negative insurance amount, causing more perp bad debt to be spread across open-interest holders via `cumulativeFundingLongX18 / cumulativeFundingShortX18` adjustments. [7](#0-6) 

- `spotEngine.socializeSubaccount(B)` is triggered (because `v.insurance ≤ 0`), diluting `cumulativeDepositsMultiplierX18` for all spot depositors. [8](#0-7) 

The insurance fund had enough to cover B's deficit without any socialization; the contamination from A's fees causes value to be destroyed for innocent open-interest holders and depositors.

---

### Likelihood Explanation

The attacker needs two things:

1. A subaccount A that can be liquidated in a step that generates fees large enough so that `insurance - fees ≤ 0` for B's finalization. This is achievable with a large position near the liquidation threshold.
2. A subaccount B that is insolvent, has all perps closed and spot assets ≤ 0 (passes the first check), but still carries spot liabilities.

Both transactions can be submitted in the same `submitTransactions` batch, guaranteeing sequential processing. No sequencer compromise is required — the attacker only needs to be a valid liquidator (no special privilege beyond being able to submit liquidation transactions).

---

### Recommendation

Scope `lastLiquidationFees` per-subaccount using a `mapping(bytes32 => int128)` keyed on the liquidatee, or reset it to zero at the start of `_finalizeSubaccount` before computing `v.canLiquidateMore`. The simplest fix:

```solidity
// In _finalizeSubaccount, replace:
v.insurance = insurance;
v.insurance -= lastLiquidationFees;

// With:
v.insurance = insurance;
// Only subtract fees that belong to THIS subaccount's last liquidation step.
// Since _finalizeSubaccount does not call _handleLiquidationPayment,
// lastLiquidationFees here always belongs to a prior (possibly different) subaccount.
// Use a per-subaccount mapping instead:
v.insurance -= lastLiquidationFeesBySubaccount[txn.liquidatee];
```

And correspondingly update `_handleLiquidationPayment` to write `lastLiquidationFeesBySubaccount[txn.liquidatee] = v.liquidationFees` and clear it after finalization.

---

### Proof of Concept

```
State setup:
  insurance = 100
  subaccount A: large spot position, can be liquidated generating fees = 80
  subaccount B: insolvent, all perps closed, spot asset balance = -30 (liability), quoteBalance = -50

Step 1 — liquidate A (non-finalization):
  _handleLiquidationPayment runs
  insurance += 80  →  insurance = 100  (net, assuming it started at 20)
  lastLiquidationFees = 80             ← global, not scoped to A

Step 2 — finalize B (productId == type(uint32).max), same batch:
  _finalizeSubaccount runs for B
  v.insurance = insurance - lastLiquidationFees = 100 - 80 = 20
  quoteBalance.amount = -50
  v.canLiquidateMore = (-50 + 20) > 0  →  false   ← WRONG (should be true: -50+100=50>0)
  spot-liability-zero check SKIPPED    ← B's -30 spot liability not caught
  perpEngine.socializeSubaccount(B, 20) called with only 20 insurance
  insuranceCover = min(20, 50) = 20
  v.insurance = 0
  v.insurance <= 0  →  spotEngine.socializeSubaccount(B) triggered
    → cumulativeDepositsMultiplierX18 diluted for all depositors (bad debt spread)
  v.insurance += lastLiquidationFees = 0 + 80 = 80
  insurance = 80   ← stored value is "correct" but damage already done

Assert: perpEngine and spotEngine have socialized 30 units of bad debt
        that should have been covered by the 100-unit insurance fund.
```

### Citations

**File:** core/contracts/ClearinghouseStorage.sol (L25-25)
```text
    int128 internal lastLiquidationFees;
```

**File:** core/contracts/ClearinghouseLiq.sol (L300-311)
```text
        // all spot assets (except USDC) must be closed out
        for (uint32 i = 1; i < v.spotIds.length; ++i) {
            uint32 spotId = v.spotIds[i];
            if (spotEngine.getRisk(spotId).longWeightInitialX18 == 0) {
                continue;
            }
            ISpotEngine.Balance memory balance = spotEngine.getBalance(
                spotId,
                txn.liquidatee
            );
            require(balance.amount <= 0, ERR_NOT_FINALIZABLE_SUBACCOUNT);
        }
```

**File:** core/contracts/ClearinghouseLiq.sol (L368-370)
```text
        v.insurance = insurance;
        v.insurance -= lastLiquidationFees;
        v.canLiquidateMore = (quoteBalance.amount + v.insurance) > 0;
```

**File:** core/contracts/ClearinghouseLiq.sol (L372-384)
```text
        if (v.canLiquidateMore) {
            for (uint32 i = 1; i < v.spotIds.length; ++i) {
                uint32 spotId = v.spotIds[i];
                ISpotEngine.Balance memory balance = spotEngine.getBalance(
                    spotId,
                    txn.liquidatee
                );
                if (spotEngine.getRisk(spotId).longWeightInitialX18 == 0) {
                    continue;
                }
                require(balance.amount == 0, ERR_NOT_FINALIZABLE_SUBACCOUNT);
            }
        }
```

**File:** core/contracts/ClearinghouseLiq.sol (L386-412)
```text
        v.insurance = perpEngine.socializeSubaccount(
            txn.liquidatee,
            v.insurance
        );

        // we can assure that quoteBalance must be non positive, because if quoteBalance.amount > 0,
        // there must be 1) no negative pnl in perps, and 2) no liabilities in spot after above actions.
        // however, in this case the liquidatee must be healthy and cannot pass the health check at
        // the beginning.
        int128 insuranceCover = MathHelper.min(
            v.insurance,
            -quoteBalance.amount
        );
        if (insuranceCover > 0) {
            v.insurance -= insuranceCover;
            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                txn.liquidatee,
                insuranceCover
            );
        }
        if (v.insurance <= 0) {
            spotEngine.socializeSubaccount(txn.liquidatee);
        }
        v.insurance += lastLiquidationFees;
        insurance = v.insurance;
        return true;
```

**File:** core/contracts/ClearinghouseLiq.sol (L579-586)
```text
        insurance += v.liquidationFees;

        // if insurance is not enough for making a subaccount healthy, we should
        // use all insurance to buy its liabilities, then socialize the subaccount
        // however, after the first step, insurance funds will be refilled a little bit
        // which blocks the second step, so we keep the fees of the last liquidation and
        // do not use this part in socialization to unblock it.
        lastLiquidationFees = v.liquidationFees;
```

**File:** core/contracts/PerpEngine.sol (L154-172)
```text
            if (balance.vQuoteBalance < 0) {
                int128 insuranceCover = MathHelper.min(
                    insurance,
                    -balance.vQuoteBalance
                );
                insurance -= insuranceCover;
                balance.vQuoteBalance += insuranceCover;
                state.availableSettle += insuranceCover;

                // actually socialize if still not enough
                if (balance.vQuoteBalance < 0) {
                    // socialize across all other participants
                    int128 fundingPerShare = -balance.vQuoteBalance.div(
                        state.openInterest
                    );
                    state.cumulativeFundingLongX18 += fundingPerShare;
                    state.cumulativeFundingShortX18 -= fundingPerShare;
                    balance.vQuoteBalance = 0;
                }
```

**File:** core/contracts/SpotEngine.sol (L255-276)
```text
            if (balance.amount < 0) {
                int128 totalDeposited = state.totalDepositsNormalized.mul(
                    state.cumulativeDepositsMultiplierX18
                );

                state.cumulativeDepositsMultiplierX18 = (totalDeposited +
                    balance.amount).div(state.totalDepositsNormalized);

                require(state.cumulativeDepositsMultiplierX18 > 0);

                state.totalBorrowsNormalized += balance.amount.div(
                    state.cumulativeBorrowsMultiplierX18
                );

                _setBalanceAndUpdateBitmap(
                    productId,
                    subaccount,
                    BalanceNormalized({amountNormalized: 0})
                );
                _setState(productId, state);
            }
        }
```
