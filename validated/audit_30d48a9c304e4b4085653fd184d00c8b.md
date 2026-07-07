### Title
`lastLiquidationFees` Not Reset Between Subaccounts Corrupts Finalization Insurance Accounting — (File: `core/contracts/ClearinghouseLiq.sol`)

---

### Summary

The `lastLiquidationFees` storage variable in `ClearinghouseLiq` is written during `_handleLiquidationPayment` for one subaccount but is **never reset to zero** after that call completes. When `_finalizeSubaccount` is subsequently invoked for a **different** subaccount, it reads the stale value left by the prior liquidation, corrupting the insurance arithmetic that governs whether losses are socialized across all depositors.

---

### Finding Description

`_handleLiquidationPayment` unconditionally overwrites the global `lastLiquidationFees` at its final line: [1](#0-0) 

`_finalizeSubaccount` then reads that value to compute the effective insurance available for the subaccount being finalized: [2](#0-1) 

At the end of `_finalizeSubaccount`, the same value is added back and written to storage: [3](#0-2) 

`lastLiquidationFees` is **never zeroed** between these two calls. Because it is a contract-level storage slot (inherited through `ClearinghouseStorage`), any liquidation of **any** subaccount that runs between the last liquidation step of subaccount B and the finalization call for subaccount B will silently overwrite the value that `_finalizeSubaccount` depends on.

The two critical decision points that consume the corrupted value are:

1. **`v.canLiquidateMore`** — if stale fees inflate the subtraction, this flag flips to `false` prematurely, causing the guard that requires all spot balances to be zero to be **skipped**: [4](#0-3) 

2. **`v.insurance <= 0` branch** — if stale fees push `v.insurance` below zero, `spotEngine.socializeSubaccount` is called when the protocol actually has sufficient insurance to cover the deficit, spreading losses to all depositors: [5](#0-4) 

The `lastLiquidationFees` add-back at line 410 means the **final `insurance` storage value** is arithmetically correct, but the intermediate decisions (socialization trigger, canLiquidateMore gate) have already been made on the wrong basis.

---

### Impact Explanation

**Medium.** Two concrete state corruptions are possible:

- **Premature socialization**: If stale `lastLiquidationFees` (from subaccount A's liquidation) exceeds the actual `insurance` balance, `v.insurance` goes negative for subaccount B's finalization. The `v.insurance <= 0` branch fires, calling `spotEngine.socializeSubaccount(txn.liquidatee)`, which mutualizes losses across all depositors even though the insurance fund was solvent enough to cover them.
- **Spot-balance guard bypass**: With `v.canLiquidateMore` incorrectly `false`, the loop that enforces `balance.amount == 0` for all spot products is skipped, allowing finalization to proceed with residual spot balances and leaving the subaccount's accounting in an inconsistent state.

---

### Likelihood Explanation

**Medium.** The subaccount state is fully observable on-chain. An attacker who holds (or can create) a liquidatable position can submit a liquidation transaction timed to be processed by the sequencer immediately before a target subaccount's finalization transaction. Because the sequencer typically processes submitted transactions in arrival order, this ordering is achievable without sequencer compromise. The attacker only needs to ensure their liquidation generates non-trivial fees (proportional to position size and the spread between oracle and liquidation price).

---

### Recommendation

Reset `lastLiquidationFees` to zero at the **start** of `_finalizeSubaccount`, before it is read:

```solidity
function _finalizeSubaccount(...) internal returns (bool) {
    if (txn.productId != type(uint32).max) {
        return false;
    }
    lastLiquidationFees = 0; // reset stale state from prior liquidations
    // ... rest of function
}
```

Alternatively, replace the global variable with a per-subaccount mapping (`mapping(bytes32 => int128) lastLiquidationFees`) so that each subaccount's finalization reads only its own last-step fees.

---

### Proof of Concept

1. Subaccount A is liquidated; `_handleLiquidationPayment` sets `lastLiquidationFees = X` (large, e.g. from a large perp position).
2. Before subaccount B's finalization transaction is processed, the sequencer picks up the attacker's liquidation of A.
3. `_finalizeSubaccount` for B executes: `v.insurance = insurance - X`. If `X > insurance`, `v.insurance < 0`.
4. `v.canLiquidateMore = (quoteBalance.amount + v.insurance) > 0` evaluates to `false` — the spot-balance zero-check is skipped.
5. `v.insurance <= 0` is `true` → `spotEngine.socializeSubaccount(B)` fires, mutualizing B's deficit across all depositors despite the insurance fund being sufficient.
6. `v.insurance += X; insurance = v.insurance` — the final `insurance` storage value is arithmetically restored, masking the corruption from off-chain monitors. [6](#0-5)

### Citations

**File:** core/contracts/ClearinghouseLiq.sol (L368-384)
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
```

**File:** core/contracts/ClearinghouseLiq.sol (L395-412)
```text
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

**File:** core/contracts/ClearinghouseLiq.sol (L598-647)
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

        if (
            (txn.amount < 0) &&
            (txn.isEncodedSpread ||
                address(productToEngine[txn.productId]) == address(spotEngine))
        ) {
            // when it's spread or spot liquidation, we need to make sure the liquidatee has
            // enough quote to buyback the liquidated amount.
            _assertCanLiquidateLiability(txn, spotEngine, perpEngine);
            _settlePositivePerpPnl(txn, spotEngine, perpEngine);
        }

        _assertLiquidationAmount(txn, spotEngine, perpEngine);

        // beyond this point, we can be sure that we can liquidate the entire
        // liquidation amount knowing that the insurance fund will remain solvent
        // subsequently we can just blast the remainder of the liquidation and
        // cover the quote balance from the insurance fund at the end
        _handleLiquidationPayment(txn, spotEngine, perpEngine);
    }
```
