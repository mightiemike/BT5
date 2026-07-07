### Title
`lastLiquidationFees` Deducted Before `v.canLiquidateMore` Check Enables Premature Subaccount Finalization and Unnecessary Bad Debt Socialization — (`File: core/contracts/ClearinghouseLiq.sol`)

---

### Summary

In `_finalizeSubaccount()`, `lastLiquidationFees` is subtracted from `v.insurance` **before** the `v.canLiquidateMore` solvency check. This mirrors the exact order-of-operations flaw in the external report: a fee/reward deduction corrupts a downstream condition check, causing the protocol to take an incorrect action — here, prematurely finalizing a subaccount and socializing bad debt that the insurance fund could have covered.

---

### Finding Description

In `ClearinghouseLiq._finalizeSubaccount()`, the protocol computes whether the liquidatee still has enough resources to be further liquidated:

```solidity
v.insurance = insurance;
v.insurance -= lastLiquidationFees;                              // line 369
v.canLiquidateMore = (quoteBalance.amount + v.insurance) > 0;   // line 370
``` [1](#0-0) 

`lastLiquidationFees` is the fee collected from the **immediately preceding** liquidation call, stored in `ClearinghouseStorage`: [2](#0-1) 

It is set at the end of `_handleLiquidationPayment()`: [3](#0-2) 

The deduction at line 369 is intentional — the comment explains it is meant to prevent the insurance fund from being "refilled" by fees and blocking socialization. However, the deduction happens **before** the `v.canLiquidateMore` check at line 370, which determines whether the liquidatee still has spot liabilities that must be fully liquidated before finalization is allowed:

```solidity
if (v.canLiquidateMore) {
    for (uint32 i = 1; i < v.spotIds.length; ++i) {
        ...
        require(balance.amount == 0, ERR_NOT_FINALIZABLE_SUBACCOUNT);  // spot liabilities must be zero
    }
}
``` [4](#0-3) 

If `lastLiquidationFees` is large enough to push `quoteBalance.amount + v.insurance` from positive to ≤ 0, `v.canLiquidateMore` becomes `false`, the spot-liability-zero check is skipped, and the subaccount is finalized with remaining spot liabilities. Those liabilities are then socialized: [5](#0-4) 

Critically, `lastLiquidationFees` is **added back** before the final write: [6](#0-5) 

This confirms the deduction is only a temporary accounting adjustment — but it incorrectly influences the `v.canLiquidateMore` gate that controls whether spot liabilities must be cleared first.

The `LIQUIDATION_FEE_FRACTION` is 50%, making `lastLiquidationFees` potentially very large relative to the insurance fund: [7](#0-6) 

---

### Impact Explanation

When `v.canLiquidateMore` is incorrectly `false`:

1. The `require(balance.amount == 0, ERR_NOT_FINALIZABLE_SUBACCOUNT)` guard for spot liabilities is bypassed.
2. `perpEngine.socializeSubaccount(txn.liquidatee, v.insurance)` is called with the artificially reduced `v.insurance`.
3. If `v.insurance <= 0` after covering the quote deficit, `spotEngine.socializeSubaccount(txn.liquidatee)` is called, spreading the remaining spot liabilities as bad debt across all profitable positions in the protocol.

The actual insurance (before deducting `lastLiquidationFees`) was sufficient to cover the liabilities — the socialization is unnecessary and directly harms all other participants holding profitable positions.

---

### Likelihood Explanation

A liquidator (unprivileged caller) can trigger this in two sequential transactions:

1. Liquidate a large position, generating a large `lastLiquidationFees` (up to 50% of the penalty × amount).
2. Immediately call `liquidateSubaccountImpl` with `txn.productId == type(uint32).max` to trigger finalization.

The liquidator does not need any special privileges. The condition is reachable whenever `lastLiquidationFees > quoteBalance.amount + insurance`, which is plausible for large positions with a near-depleted insurance fund — precisely the scenario where socialization is most harmful.

---

### Recommendation

Move the `lastLiquidationFees` deduction to **after** the `v.canLiquidateMore` check, so the check uses the true available insurance:

```diff
 v.insurance = insurance;
-v.insurance -= lastLiquidationFees;
 v.canLiquidateMore = (quoteBalance.amount + v.insurance) > 0;

 if (v.canLiquidateMore) {
     for (uint32 i = 1; i < v.spotIds.length; ++i) {
         ...
         require(balance.amount == 0, ERR_NOT_FINALIZABLE_SUBACCOUNT);
     }
 }

+v.insurance -= lastLiquidationFees;
 v.insurance = perpEngine.socializeSubaccount(txn.liquidatee, v.insurance);
 ...
```

This ensures the gate that prevents premature finalization uses the full insurance value, while the socialization step still correctly excludes `lastLiquidationFees`.

---

### Proof of Concept

**Setup:**
- Liquidatee has `quoteBalance.amount = -80` (owes 80 USDC) and a spot liability (e.g., ETH balance = -1 ETH, worth 100 USDC).
- `insurance = 100`
- Liquidator performs a perp liquidation generating `lastLiquidationFees = 30`.

**Execution of `_finalizeSubaccount` (with bug):**

```
v.insurance = 100
v.insurance -= 30  →  v.insurance = 70
v.canLiquidateMore = (-80 + 70) > 0  →  false
```

The spot-liability check is skipped. `spotEngine.socializeSubaccount` is called, spreading the -1 ETH liability as bad debt.

**Correct behavior (without bug):**

```
v.canLiquidateMore = (-80 + 100) > 0  →  true
require(ETH balance == 0)  →  REVERTS with ERR_NOT_FINALIZABLE_SUBACCOUNT
```

The liquidator is forced to liquidate the ETH liability first, preventing unnecessary socialization. The 100 USDC insurance is sufficient to cover the 80 USDC quote deficit, leaving 20 USDC in the fund.

### Citations

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

**File:** core/contracts/ClearinghouseLiq.sol (L407-409)
```text
        if (v.insurance <= 0) {
            spotEngine.socializeSubaccount(txn.liquidatee);
        }
```

**File:** core/contracts/ClearinghouseLiq.sol (L410-411)
```text
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

**File:** core/contracts/ClearinghouseStorage.sol (L25-25)
```text
    int128 internal lastLiquidationFees;
```

**File:** core/contracts/common/Constants.sol (L36-36)
```text
int128 constant LIQUIDATION_FEE_FRACTION = 500_000_000_000_000_000; // 50%
```
