### Title
Stale `lastLiquidationFees` Across Subaccounts Corrupts Finalization Spot-Balance Check — (File: `core/contracts/ClearinghouseLiq.sol`)

---

### Summary

`lastLiquidationFees` is a global storage variable set during any liquidation but never reset after finalization. When `_finalizeSubaccount` is called for subaccount B, it reads `lastLiquidationFees` left over from a prior liquidation of a completely different subaccount A. This stale value drives the `v.canLiquidateMore` gate that controls a mandatory spot-balance check. If the stale fee is large enough to flip `v.canLiquidateMore` to `false`, the spot-balance check is bypassed, allowing premature finalization of a subaccount that still holds spot assets and corrupting the insurance fund.

---

### Finding Description

In `liquidateSubaccountImpl`, the call order is:

```
_finalizeSubaccount(txn, ...)   // reads lastLiquidationFees
...
_handleLiquidationPayment(txn, ...)  // writes lastLiquidationFees
```

`_handleLiquidationPayment` sets the global variable at the end of every non-finalization liquidation:

```solidity
// ClearinghouseLiq.sol line 586
lastLiquidationFees = v.liquidationFees;
```

`_finalizeSubaccount` then consumes that value to compute `v.canLiquidateMore`:

```solidity
// ClearinghouseLiq.sol lines 368-370
v.insurance = insurance;
v.insurance -= lastLiquidationFees;
v.canLiquidateMore = (quoteBalance.amount + v.insurance) > 0;
```

`v.canLiquidateMore` gates a critical invariant check:

```solidity
// ClearinghouseLiq.sol lines 372-384
if (v.canLiquidateMore) {
    for (uint32 i = 1; i < v.spotIds.length; ++i) {
        ...
        require(balance.amount == 0, ERR_NOT_FINALIZABLE_SUBACCOUNT);
    }
}
```

After finalization completes, `lastLiquidationFees` is **never reset to zero**. The add-back at line 410 restores `insurance` to its correct value, but `lastLiquidationFees` persists in storage with the value from whichever liquidation last ran — regardless of which subaccount was involved.

Because `lastLiquidationFees` is a single global slot (not a per-subaccount mapping), any liquidation of any subaccount overwrites it. The next finalization call — even for a completely unrelated subaccount — reads this contaminated value.

---

### Impact Explanation

If `lastLiquidationFees` (stale from subaccount A's liquidation) satisfies:

```
lastLiquidationFees  >=  quoteBalance.amount + insurance
```

then `v.canLiquidateMore` evaluates to `false` for subaccount B's finalization. The `require(balance.amount == 0, ERR_NOT_FINALIZABLE_SUBACCOUNT)` loop is skipped entirely. `perpEngine.socializeSubaccount` is then called while B still holds non-zero spot assets. The insurance fund absorbs B's perp losses prematurely, while B's remaining spot assets are left unaccounted. This directly corrupts the `insurance` storage variable and the protocol's solvency invariant.

---

### Likelihood Explanation

In any live market downturn, multiple subaccounts become liquidatable simultaneously. A liquidator (unprivileged caller) naturally liquidates one subaccount (setting `lastLiquidationFees` to a large value from a large position) and then submits a finalization transaction for a different underwater subaccount. No privileged access, no sequencer compromise, and no social engineering is required — only two sequential liquidation calls in the normal protocol flow.

---

### Recommendation

Reset `lastLiquidationFees` to zero at the end of `_finalizeSubaccount`, after the final `insurance = v.insurance` assignment:

```solidity
insurance = v.insurance;
lastLiquidationFees = 0;  // reset so stale value cannot affect future finalizations
return true;
```

Alternatively, convert `lastLiquidationFees` to a per-subaccount mapping (`mapping(bytes32 => int128) lastLiquidationFees`) so that each subaccount's finalization only reads its own last fee.

---

### Proof of Concept

1. Subaccount A holds a large perp position and becomes liquidatable.
2. Liquidator calls `liquidateSubaccountImpl` for A (non-finalization, `productId != max`). `_handleLiquidationPayment` sets `lastLiquidationFees = F_A` (a large value proportional to A's position size). [1](#0-0) 
3. Subaccount B is also underwater. Liquidator calls `liquidateSubaccountImpl` for B with `productId == type(uint32).max` (finalization). `_finalizeSubaccount` is entered. [2](#0-1) 
4. Inside `_finalizeSubaccount`, `v.insurance = insurance - lastLiquidationFees` uses the stale `F_A`. If `F_A >= quoteBalance.amount + insurance`, then `v.canLiquidateMore = false`. [3](#0-2) 
5. The spot-balance loop (`require(balance.amount == 0, ERR_NOT_FINALIZABLE_SUBACCOUNT)`) is skipped. [4](#0-3) 
6. `perpEngine.socializeSubaccount(txn.liquidatee, v.insurance)` is called while B still holds non-zero spot assets. The insurance fund is depleted to cover B's perp losses prematurely, corrupting the global `insurance` balance. [5](#0-4)

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

**File:** core/contracts/ClearinghouseLiq.sol (L583-586)
```text
        // however, after the first step, insurance funds will be refilled a little bit
        // which blocks the second step, so we keep the fees of the last liquidation and
        // do not use this part in socialization to unblock it.
        lastLiquidationFees = v.liquidationFees;
```

**File:** core/contracts/ClearinghouseLiq.sol (L620-627)
```text
        if (_finalizeSubaccount(txn, spotEngine, perpEngine)) {
            if (RiskHelper.isIsolatedSubaccount(txn.liquidatee)) {
                IOffchainExchange(
                    IEndpoint(getEndpoint()).getOffchainExchange()
                ).tryCloseIsolatedSubaccount(txn.liquidatee);
            }
            return;
        }
```
