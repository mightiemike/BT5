### Title
Liquidator Quote Balance Drained to Negative During Subaccount Finalization Due to Missing Sender Health Check in `_finalizeSubaccount` — (`core/contracts/ClearinghouseLiq.sol`)

---

### Summary

`_finalizeSubaccount` unconditionally settles all positive `vQuoteBalance` from the liquidatee's perp positions against the liquidator's (`txn.sender`) quote balance with no cap and no post-settlement health check on the liquidator. The health check that protects the liquidator in the normal liquidation path (`_handleLiquidationPayment`) is entirely bypassed when finalization succeeds. A crafted liquidatee with large aggregate positive `vQuoteBalance` across many perp products can drive the liquidator's quote deeply negative, rendering the liquidator unhealthy and subsequently liquidatable.

---

### Finding Description

In `liquidateSubaccountImpl`, when `txn.productId == type(uint32).max`, execution enters `_finalizeSubaccount` and returns early if it succeeds, **never reaching `_handleLiquidationPayment`**:

```solidity
// ClearinghouseLiq.sol lines 620-627
if (_finalizeSubaccount(txn, spotEngine, perpEngine)) {
    ...
    return;   // <-- _handleLiquidationPayment is never called
}
``` [1](#0-0) 

Inside `_finalizeSubaccount`, the positive-PnL loop (lines 322–338) iterates every perp product and, for each one with `vQuoteBalance > 0`, calls `_settlePnlAgainstLiquidator` with the **full** `vQuoteBalance` — no cap, no running total check:

```solidity
// lines 322-338
for (uint32 i = 0; i < v.perpIds.length; ++i) {
    ...
    if (balance.vQuoteBalance > 0) {
        _settlePnlAgainstLiquidator(txn, perpId, balance.vQuoteBalance, ...);
    }
}
``` [2](#0-1) 

`_settlePnlAgainstLiquidator` unconditionally deducts `pnl` from the liquidator's quote:

```solidity
// lines 259-270
spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.liquidatee, pnl);
spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -pnl);  // no floor
``` [3](#0-2) 

The only liquidator health check in the entire file lives in `_handleLiquidationPayment`:

```solidity
// lines 574-576
require(
    txn.sender == N_ACCOUNT || !isUnderInitial(txn.sender),
    ERR_SUBACCT_HEALTH
);
``` [4](#0-3) 

This check is **never reached** on the finalization path. After the positive-PnL loop completes, `_finalizeSubaccount` continues to handle negative PnL, insurance, and socialization — all without ever asserting `!isUnderInitial(txn.sender)`. [5](#0-4) 

---

### Impact Explanation

An attacker who controls the liquidatee can craft a subaccount satisfying all finalization preconditions (all perp `amount == 0`, all spot assets `<= 0`, under maintenance health) while holding large positive `vQuoteBalance` across many perp products. When any liquidator submits a finalization transaction, the liquidator's quote is decremented by the full aggregate positive PnL. If that aggregate exceeds the liquidator's quote balance, the liquidator's quote goes negative, the liquidator's subaccount becomes unhealthy, and the liquidator is itself eligible for liquidation — causing real asset loss for the liquidator.

The `IPerpEngine.Balance` struct confirms `vQuoteBalance` is a standalone `int128` field independent of the position `amount`: [6](#0-5) 

A subaccount with `amount == 0` but `vQuoteBalance > 0` is a normal, reachable state (residual PnL after a position is closed). The `_updateBalance` logic in `PerpEngineState` confirms `vQuoteBalance` persists independently of `amount`: [7](#0-6) 

---

### Likelihood Explanation

The preconditions are reachable through normal protocol usage:
- Open perp positions across N products, accumulate positive `vQuoteBalance` (e.g., by being long during a price rise), then close positions (`amount → 0`).
- Simultaneously hold spot liabilities large enough to push maintenance health negative.
- The subaccount now satisfies every `require` in `_finalizeSubaccount` (lines 301–320) and enters the positive-PnL loop.

Any liquidator with quote < total positive `vQuoteBalance` is at risk. Because the sequencer submits liquidation transactions and liquidators cannot easily inspect aggregate `vQuoteBalance` across all perp products before submitting, this is a realistic griefing/drain vector.

---

### Recommendation

Add a liquidator health check immediately after the positive-PnL settlement loop in `_finalizeSubaccount`, mirroring the guard already present in `_handleLiquidationPayment`:

```solidity
// after line 338, before reading quoteBalance
require(
    txn.sender == N_ACCOUNT || !isUnderInitial(txn.sender),
    ERR_SUBACCT_HEALTH
);
```

Alternatively, cap the total positive PnL settled against the liquidator to the liquidator's available quote balance before entering the loop, analogous to the capping logic already applied to the negative-PnL loop (lines 352–365). [8](#0-7) 

---

### Proof of Concept

```solidity
// Hardhat test (pseudocode, unmodified protocol)
// Setup:
//   - liquidatee: 10 perp products, each balance.amount = 0, balance.vQuoteBalance = 1e18
//   - liquidatee: spot liability large enough for maintenance health < 0
//   - liquidator (txn.sender): quote balance = 5e18

// Trigger finalization:
//   txn.productId = type(uint32).max

// After liquidateSubaccountImpl:
//   liquidator quote = 5e18 - 10e18 = -5e18   (negative, no revert)
//   liquidator isUnderInitial() == true         (no check fired)
//   liquidatee quote += 10e18                   (positive PnL fully transferred)

// Assert:
assert(spotEngine.getBalance(QUOTE_PRODUCT_ID, liquidator).amount == -5e18);
// No ERR_SUBACCT_HEALTH revert was thrown during the loop.
```

The loop at lines 322–338 iterates all 10 products, calling `_settlePnlAgainstLiquidator` each time, deducting `1e18` per iteration from the liquidator's quote with no guard. The finalization path returns at line 626 without ever invoking the health check at lines 574–576. [2](#0-1) [1](#0-0)

### Citations

**File:** core/contracts/ClearinghouseLiq.sol (L266-269)
```text
        perpEngine.updateBalance(perpId, txn.liquidatee, 0, -pnl);
        perpEngine.updateBalance(perpId, txn.sender, 0, pnl);
        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.liquidatee, pnl);
        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -pnl);
```

**File:** core/contracts/ClearinghouseLiq.sol (L322-338)
```text
        // settle all positive pnl
        for (uint32 i = 0; i < v.perpIds.length; ++i) {
            uint32 perpId = v.perpIds[i];
            IPerpEngine.Balance memory balance = perpEngine.getBalance(
                perpId,
                txn.liquidatee
            );
            if (balance.vQuoteBalance > 0) {
                _settlePnlAgainstLiquidator(
                    txn,
                    perpId,
                    balance.vQuoteBalance,
                    spotEngine,
                    perpEngine
                );
            }
        }
```

**File:** core/contracts/ClearinghouseLiq.sol (L340-413)
```text
        ISpotEngine.Balance memory quoteBalance = spotEngine.getBalance(
            QUOTE_PRODUCT_ID,
            txn.liquidatee
        );

        // settle all negative pnl until quote balance becomes 0
        for (uint32 i = 0; i < v.perpIds.length; ++i) {
            uint32 perpId = v.perpIds[i];
            IPerpEngine.Balance memory balance = perpEngine.getBalance(
                perpId,
                txn.liquidatee
            );
            if (balance.vQuoteBalance < 0 && quoteBalance.amount > 0) {
                int128 canSettle = MathHelper.max(
                    balance.vQuoteBalance,
                    -quoteBalance.amount
                );
                _settlePnlAgainstLiquidator(
                    txn,
                    perpId,
                    canSettle,
                    spotEngine,
                    perpEngine
                );
                quoteBalance.amount += canSettle;
            }
        }

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
    }
```

**File:** core/contracts/ClearinghouseLiq.sol (L574-577)
```text
        require(
            txn.sender == N_ACCOUNT || !isUnderInitial(txn.sender),
            ERR_SUBACCT_HEALTH
        );
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

**File:** core/contracts/interfaces/engine/IPerpEngine.sol (L22-26)
```text
    struct Balance {
        int128 amount;
        int128 vQuoteBalance;
        int128 lastCumulativeFundingX18;
    }
```

**File:** core/contracts/PerpEngineState.sol (L23-52)
```text
    function _updateBalance(
        State memory state,
        Balance memory balance,
        int128 balanceDelta,
        int128 vQuoteDelta
    ) internal pure {
        // pre update
        state.openInterest -= balance.amount.abs();
        int128 cumulativeFundingAmountX18 = (balance.amount > 0)
            ? state.cumulativeFundingLongX18
            : state.cumulativeFundingShortX18;
        int128 diffX18 = cumulativeFundingAmountX18 -
            balance.lastCumulativeFundingX18;
        int128 deltaQuote = vQuoteDelta - diffX18.mul(balance.amount);

        // apply delta
        balance.amount += balanceDelta;

        // apply vquote
        balance.vQuoteBalance += deltaQuote;

        // post update
        if (balance.amount > 0) {
            state.openInterest += balance.amount;
            balance.lastCumulativeFundingX18 = state.cumulativeFundingLongX18;
        } else {
            state.openInterest -= balance.amount;
            balance.lastCumulativeFundingX18 = state.cumulativeFundingShortX18;
        }
    }
```
