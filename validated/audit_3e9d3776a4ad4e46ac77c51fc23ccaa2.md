Looking at the code carefully to trace the exact storage mutation and determine if the invariant can break.

### Title
Cross-Subaccount `lastLiquidationFees` Contamination Bypasses Spot-Balance Finalization Guard — (`core/contracts/ClearinghouseLiq.sol`)

---

### Summary

`lastLiquidationFees` is a single global storage slot shared across all subaccount liquidations. `_finalizeSubaccount` unconditionally subtracts it from `insurance` to compute `v.canLiquidateMore`, without verifying that the stored value belongs to the subaccount currently being finalized. A liquidator who processes a normal liquidation of subaccount A immediately before submitting a finalization (`productId == type(uint32).max`) for a different insolvent subaccount B can make `v.canLiquidateMore` evaluate to `false` even when the real insurance fund is sufficient, silently skipping the spot-balance-zero guard and allowing B to be socialized while still holding non-zero spot liabilities.

---

### Finding Description

**Storage layout**

`lastLiquidationFees` is declared as a plain `int128` in `ClearinghouseStorage` with no per-subaccount scoping: [1](#0-0) 

**Write site — `_handleLiquidationPayment`**

Every successful non-finalization liquidation overwrites this slot with the fees earned from that specific liquidation, regardless of which subaccount was the liquidatee: [2](#0-1) 

**Read site — `_finalizeSubaccount`**

When `productId == type(uint32).max`, the function blindly subtracts the global `lastLiquidationFees` from `insurance` to decide whether the insurance fund is "sufficient": [3](#0-2) 

The design comment (lines 582–586) makes clear the intent: `lastLiquidationFees` is supposed to represent the fees from the **last liquidation step of the same subaccount** being finalized, so that a small insurance top-up from that step does not block socialization. The code never enforces this assumption.

**The guarded check that gets skipped**

The spot-balance-zero check is only executed when `v.canLiquidateMore == true`: [4](#0-3) 

If `v.canLiquidateMore` is forced to `false` by an inflated `lastLiquidationFees`, this `require(balance.amount == 0)` is never reached, and `_finalizeSubaccount` proceeds to socialize a subaccount that still holds non-zero spot liabilities.

---

### Impact Explanation

When the spot-balance check is bypassed, `spotEngine.socializeSubaccount(txn.liquidatee)` is called on a subaccount that still has negative spot balances (e.g., a BTC liability). Socialization spreads that bad debt proportionally across all open-interest holders, permanently destroying value for uninvolved parties. The insurance fund is also consumed at the wrong rate: `v.insurance` is computed as `insurance - fees_from_A` rather than the true available balance, so the cover applied to B's quote deficit is understated, leaving a larger residual to be socialized. [5](#0-4) 

---

### Likelihood Explanation

The attacker needs only to:
1. Be an active liquidator (no special role required beyond submitting signed `LiquidateSubaccount` transactions through `Endpoint`).
2. Find or manufacture a situation where subaccount A's liquidation fees are large enough that `insurance - fees_from_A <= -quoteBalance_B`.
3. Submit both transactions in the same `submitTransactions` batch so the sequencer processes them back-to-back with no intervening state change.

All three conditions are achievable in normal protocol operation during periods of high volatility when insurance fees are large and multiple accounts are simultaneously insolvent.

---

### Recommendation

Scope `lastLiquidationFees` to the subaccount being liquidated, or reset it at the start of each `liquidateSubaccountImpl` call before the finalization branch is reached. The simplest fix is to zero out `lastLiquidationFees` at the top of `liquidateSubaccountImpl` and only set it inside `_handleLiquidationPayment` for the same call frame, ensuring that a finalization transaction for subaccount B always sees `lastLiquidationFees == 0` unless the immediately preceding action in the same call was a non-finalization liquidation of B itself.

---

### Proof of Concept

```
Initial state:
  insurance = 500
  lastLiquidationFees = 0

Tx 1 — LiquidateSubaccount(sender=L, liquidatee=A, productId=2, amount=100)
  _handleLiquidationPayment:
    v.liquidationFees = 600
    insurance = 500 + 600 = 1100
    lastLiquidationFees = 600          ← global slot written

Tx 2 — LiquidateSubaccount(sender=L, liquidatee=B, productId=type(uint32).max, amount=0)
  _finalizeSubaccount:
    quoteBalance(B) = -800
    v.insurance = 1100 - 600 = 500     ← uses A's fees, not B's
    v.canLiquidateMore = (-800 + 500) > 0  →  false

    // spot-balance-zero check SKIPPED
    // B still holds spotBalance(BTC) = -50

    perpEngine.socializeSubaccount(B, 500)
    insuranceCover = min(500, 800) = 500
    v.insurance = 0
    spotEngine.updateBalance(QUOTE, B, +500)  // quote: -800 → -300
    v.insurance <= 0  →  spotEngine.socializeSubaccount(B)
    // B's -50 BTC liability is socialized across all OI holders

Expected (with correct insurance = 1100):
    v.canLiquidateMore = (-800 + 1100) > 0  →  true
    require(spotBalance(BTC, B) == 0)  →  REVERT
    // finalization blocked; liquidator must close BTC position first
```

### Citations

**File:** core/contracts/ClearinghouseStorage.sol (L23-26)
```text
    int128 internal insurance;

    int128 internal lastLiquidationFees;

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

**File:** core/contracts/ClearinghouseLiq.sol (L386-411)
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
