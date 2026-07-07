### Title
Stale Global `lastLiquidationFees` Causes Understated Insurance in `_finalizeSubaccount`, Triggering Premature Socialization — (`core/contracts/ClearinghouseLiq.sol`)

---

### Summary

`lastLiquidationFees` is a single global storage slot in `ClearinghouseStorage`. It is written unconditionally at the end of every `_handleLiquidationPayment` call, regardless of which subaccount is being liquidated. When `_finalizeSubaccount` is called for **any** subaccount with `productId == type(uint32).max`, it blindly subtracts this global value from `insurance` before socialization. If the value was set by a prior liquidation of a **different** subaccount, the finalization uses an understated insurance amount, potentially triggering `spotEngine.socializeSubaccount` when the full insurance would have been sufficient to cover the bad debt.

---

### Finding Description

**Storage layout:**

`lastLiquidationFees` is declared as a single protocol-wide `int128` in `ClearinghouseStorage`: [1](#0-0) 

**Write site — end of every non-finalize liquidation:**

At the end of `_handleLiquidationPayment`, the fees from the current liquidation step are written unconditionally to this global slot: [2](#0-1) 

The comment (lines 581–585) explains the design intent: when the **same** subaccount is being liquidated in multiple steps, the last step adds fees to `insurance`, which would otherwise prevent the subsequent finalization from triggering socialization. Excluding those fees "unblocks" the socialization check. This is correct for the same-subaccount case.

**Read site — `_finalizeSubaccount`:**

When `productId == type(uint32).max`, the function subtracts `lastLiquidationFees` from `insurance` before passing it to `perpEngine.socializeSubaccount` and computing `insuranceCover`: [3](#0-2) [4](#0-3) 

The value is added back at line 410 before writing to storage, so the net effect on `insurance` is correct **only when** `v.insurance` remains positive throughout. If the subtraction causes `v.insurance` to drop to ≤ 0 at line 407, `spotEngine.socializeSubaccount` is called — spreading losses to depositors — even though the full `insurance` would have been sufficient.

**The bug:** There is no per-subaccount scoping. `lastLiquidationFees` is overwritten by every liquidation of every subaccount. A finalization of SubaccountB will use the fees from the most recent liquidation of SubaccountA.

---

### Impact Explanation

Concrete numerical example:

| Variable | Value |
|---|---|
| `insurance` before SubaccountA liquidation | 1 500 USDC |
| SubaccountA liquidation fees | 1 000 USDC |
| `insurance` after SubaccountA liquidation | 2 500 USDC |
| `lastLiquidationFees` | 1 000 USDC |
| SubaccountB bad debt (`-quoteBalance`) | 2 000 USDC |

**Buggy execution (SubaccountB finalization):**
- `v.insurance = 2500 − 1000 = 1500`
- `insuranceCover = min(1500, 2000) = 1500` → covers only 1 500 USDC
- `v.insurance = 0` → `spotEngine.socializeSubaccount(SubaccountB)` called, spreading 500 USDC to depositors
- `v.insurance += 1000 = 1000` → `insurance = 1000`

**Correct execution (no stale fees):**
- `v.insurance = 2500`
- `insuranceCover = min(2500, 2000) = 2000` → covers all 2 000 USDC
- `v.insurance = 500 > 0` → **no socialization**
- `insurance = 500`

**Result of bug:** 500 USDC of bad debt is incorrectly socialized to depositors. The insurance fund retains 1 000 USDC it should have spent. This directly breaks the solvency guarantee: solvent depositors absorb losses that the insurance fund was obligated to cover.

---

### Likelihood Explanation

This is reachable through normal protocol operation without any privileged access:

1. Any liquidator can submit a `LiquidateSubaccount` transaction for SubaccountA (a genuinely unhealthy subaccount), generating a large `lastLiquidationFees`.
2. The same or a different liquidator immediately submits `LiquidateSubaccount` with `productId = type(uint32).max` for SubaccountB (a different subaccount that is ready for finalization with bad debt).
3. The sequencer processes them in order; no atomicity or special privilege is required.

This can also occur non-maliciously during any period of market stress when multiple subaccounts are being liquidated concurrently.

---

### Recommendation

Scope `lastLiquidationFees` to the subaccount being liquidated. The simplest fix is to reset `lastLiquidationFees` to zero at the start of `_finalizeSubaccount` if the subaccount being finalized is not the same one whose fees were last recorded. Alternatively, store `lastLiquidationFees` as a `mapping(bytes32 => int128)` keyed by subaccount, and read only the entry for `txn.liquidatee` in `_finalizeSubaccount`.

---

### Proof of Concept

```solidity
// 1. SubaccountA is unhealthy. Liquidator calls liquidateSubaccountImpl for SubaccountA
//    with a large position. _handleLiquidationPayment runs:
//      insurance += 1000e18;          // fees added (line 579)
//      lastLiquidationFees = 1000e18; // global slot overwritten (line 586)
//    State: insurance = 2500e18, lastLiquidationFees = 1000e18

// 2. SubaccountB has all positions closed, quoteBalance = -2000e18 (bad debt).
//    Liquidator calls liquidateSubaccountImpl(txn) where txn.productId = type(uint32).max
//    and txn.liquidatee = SubaccountB.

// Inside _finalizeSubaccount:
//   v.insurance = insurance;           // 2500e18  (line 368)
//   v.insurance -= lastLiquidationFees; // 1500e18  (line 369) ← stale fees from SubaccountA
//   v.insurance = perpEngine.socializeSubaccount(SubaccountB, 1500e18); // returns 1500e18
//   insuranceCover = min(1500e18, 2000e18) = 1500e18;
//   v.insurance -= 1500e18;            // 0
//   // v.insurance <= 0 → spotEngine.socializeSubaccount(SubaccountB) called!
//   //   500e18 of losses spread to depositors
//   v.insurance += lastLiquidationFees; // 1000e18  (line 410)
//   insurance = 1000e18;               // (line 411)

// Assert: insurance should be 500e18 (2500 - 2000), not 1000e18.
// Assert: spotEngine.socializeSubaccount should NOT have been called.
// Depositors absorbed 500e18 USDC that the insurance fund should have covered.
```

### Citations

**File:** core/contracts/ClearinghouseStorage.sol (L23-25)
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
