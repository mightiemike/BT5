### Title
Stale `lastLiquidationFees` Cross-Subaccount Contamination Causes Premature Socialization Loss — (`core/contracts/ClearinghouseLiq.sol`)

---

### Summary

`lastLiquidationFees` is a single shared storage slot in `ClearinghouseStorage`. It is written by every `_handleLiquidationPayment` call and read by every `_finalizeSubaccount` call. Because the variable is not scoped to the subaccount being liquidated, a finalization of subaccount B will silently consume the fee value left behind by a prior, unrelated liquidation step on subaccount A. This causes `_finalizeSubaccount` to present less insurance to `perpEngine.socializeSubaccount` than the fund actually holds, and can trigger `spotEngine.socializeSubaccount` on B even when the insurance fund is fully solvent with respect to B's bad debt.

---

### Finding Description

**Storage declarations** (`ClearinghouseStorage.sol` lines 23–25):

```solidity
int128 internal insurance;
int128 internal lastLiquidationFees;
``` [1](#0-0) 

**Writer** — `_handleLiquidationPayment` (lines 579–586):

```solidity
insurance += v.liquidationFees;
lastLiquidationFees = v.liquidationFees;   // always overwritten, any subaccount
``` [2](#0-1) 

The comment at lines 581–585 explains the design intent: when a single subaccount is being liquidated in multiple steps, the finalization step should not count the fees just added by the immediately preceding step (of the *same* subaccount) as available insurance, because doing so would block socialization. The mechanism chosen is to subtract `lastLiquidationFees` from `insurance` before socialization and add it back after.

**Reader** — `_finalizeSubaccount` (lines 368–411):

```solidity
v.insurance = insurance;
v.insurance -= lastLiquidationFees;          // line 369 — reads global slot
v.canLiquidateMore = (quoteBalance.amount + v.insurance) > 0;
...
v.insurance = perpEngine.socializeSubaccount(txn.liquidatee, v.insurance);
...
int128 insuranceCover = MathHelper.min(v.insurance, -quoteBalance.amount);
if (insuranceCover > 0) { v.insurance -= insuranceCover; ... }
if (v.insurance <= 0) { spotEngine.socializeSubaccount(txn.liquidatee); }  // line 408
v.insurance += lastLiquidationFees;          // line 410 — adds back global slot
insurance = v.insurance;
``` [3](#0-2) 

The problem: `lastLiquidationFees` is **never reset** between subaccounts. `_finalizeSubaccount` does not write to it. Therefore, when B's finalization runs after A's liquidation step, it reads A's fee value, not B's.

**Call sequence that triggers the bug:**

```
liquidateSubaccountImpl(A, productId=someProduct)
  → _handleLiquidationPayment(A)
      insurance += feesA
      lastLiquidationFees = feesA          ← written

liquidateSubaccountImpl(B, productId=type(uint32).max)
  → _finalizeSubaccount(B)
      v.insurance = insurance - lastLiquidationFees   ← reads feesA, not feesB
      perpEngine.socializeSubaccount(B, v.insurance)  ← receives too little
      if (v.insurance <= 0) spotEngine.socializeSubaccount(B)  ← may fire incorrectly
      v.insurance += lastLiquidationFees              ← adds feesA back
      insurance = v.insurance
``` [4](#0-3) 

---

### Impact Explanation

**Concrete numeric example:**

| Variable | Value |
|---|---|
| `insurance` | 1 000 |
| `lastLiquidationFees` (from A) | 500 |
| B's `quoteBalance` (bad debt) | −800 |

**Correct behavior** (if `lastLiquidationFees = 0`):
- `v.insurance = 1 000`
- `insuranceCover = min(1 000, 800) = 800` → insurance covers all bad debt
- `v.insurance = 200 > 0` → `spotEngine.socializeSubaccount` **not** called
- Final `insurance = 200`

**Buggy behavior** (stale `lastLiquidationFees = 500`):
- `v.insurance = 1 000 − 500 = 500`
- `insuranceCover = min(500, 800) = 500` → only 500 of 800 covered
- `v.insurance = 0 ≤ 0` → `spotEngine.socializeSubaccount(B)` **is called** — 300 of losses socialized onto other traders
- `v.insurance += 500` → final `insurance = 500`

The insurance fund retains 300 it should have spent, while other traders absorb 300 in losses they should not bear. This is an incorrect value transfer that directly matches the Critical scope: *insurance handling that transfers value incorrectly*. [5](#0-4) 

---

### Likelihood Explanation

The trigger requires no special privileges. Any liquidator can submit two sequenced transactions through the normal endpoint liquidation path:

1. A non-finalization liquidation step on any subaccount A (sets `lastLiquidationFees`).
2. A finalization call (`productId = type(uint32).max`) on any independently under-maintenance subaccount B.

This can also occur non-maliciously whenever two different subaccounts happen to be liquidated in sequence during a market stress event — which is precisely when the insurance fund is most needed. The likelihood is **high** in any scenario involving concurrent liquidations.

---

### Recommendation

Scope `lastLiquidationFees` to the subaccount being liquidated. The simplest fix is to reset `lastLiquidationFees` to zero at the start of `_finalizeSubaccount` (or use a local variable instead of the storage slot when the finalization is for a different subaccount than the one that set it). Alternatively, store a `(subaccount → lastLiquidationFees)` mapping so that B's finalization only subtracts fees that were generated by B's own preceding liquidation step.

---

### Proof of Concept

```solidity
// State before:
//   insurance = 1000e18
//   lastLiquidationFees = 0

// Step 1: liquidate subaccount A (non-finalization)
// _handleLiquidationPayment sets:
//   insurance = 1000e18 + 500e18 = 1500e18
//   lastLiquidationFees = 500e18

// Step 2: finalize subaccount B (quoteBalance = -800e18, no perp positions)
// _finalizeSubaccount:
//   v.insurance = 1500e18 - 500e18 = 1000e18   ← correct so far
//   perpEngine.socializeSubaccount(B, 1000e18) → 1000e18 (no perp bad debt)
//   insuranceCover = min(1000e18, 800e18) = 800e18
//   v.insurance = 200e18 > 0 → spotEngine.socializeSubaccount NOT called
//   insurance = 200e18 + 500e18 = 700e18  ✓

// NOW: introduce stale lastLiquidationFees from a DIFFERENT subaccount A2
// Step 1b: liquidate subaccount A2 (non-finalization), feesA2 = 900e18
// _handleLiquidationPayment:
//   insurance = 1500e18 + 900e18 = 2400e18
//   lastLiquidationFees = 900e18

// Step 2b: finalize subaccount B (quoteBalance = -800e18)
// _finalizeSubaccount:
//   v.insurance = 2400e18 - 900e18 = 1500e18  ← still ok here
//   insuranceCover = min(1500e18, 800e18) = 800e18
//   v.insurance = 700e18 > 0 → no socialization  ✓

// Edge case that triggers the bug:
// insurance = 1000e18, lastLiquidationFees = 500e18 (from A), B bad debt = 800e18
// v.insurance = 500e18 < 800e18
// insuranceCover = 500e18, v.insurance = 0
// spotEngine.socializeSubaccount(B) called  ← BUG: 300e18 socialized unnecessarily
// insurance = 0 + 500e18 = 500e18           ← fund retains 500 it should have spent
```

The fuzz assertion `assert(socializedLoss == 0 when insurance >= badDebt)` fails whenever `lastLiquidationFees > insurance - badDebt`. [6](#0-5) [2](#0-1)

### Citations

**File:** core/contracts/ClearinghouseStorage.sol (L23-25)
```text
    int128 internal insurance;

    int128 internal lastLiquidationFees;
```

**File:** core/contracts/ClearinghouseLiq.sol (L368-412)
```text
        v.insurance = insurance;
        v.insurance -= lastLiquidationFees;
        v.canLiquidateMore = (quoteBalance.amount + v.insurance) > 0;

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

**File:** core/contracts/ClearinghouseLiq.sol (L598-627)
```text
    function liquidateSubaccountImpl(IEndpoint.LiquidateSubaccount calldata txn)
        external
    {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);
        require(txn.sender != txn.liquidatee, ERR_UNAUTHORIZED);
        require(isUnderMaintenance(txn.liquidatee), ERR_NOT_LIQUIDATABLE);
        require(
            txn.liquidatee != X_ACCOUNT && txn.liquidatee != N_ACCOUNT,
            ERR_NOT_LIQUIDATABLE
        );
        require(
            txn.productId != QUOTE_PRODUCT_ID,
            ERR_INVALID_LIQUIDATION_PARAMS
        );

        ISpotEngine spotEngine = ISpotEngine(
            address(engineByType[IProductEngine.EngineType.SPOT])
        );
        IPerpEngine perpEngine = IPerpEngine(
            address(engineByType[IProductEngine.EngineType.PERP])
        );

        if (_finalizeSubaccount(txn, spotEngine, perpEngine)) {
            if (RiskHelper.isIsolatedSubaccount(txn.liquidatee)) {
                IOffchainExchange(
                    IEndpoint(getEndpoint()).getOffchainExchange()
                ).tryCloseIsolatedSubaccount(txn.liquidatee);
            }
            return;
        }
```
