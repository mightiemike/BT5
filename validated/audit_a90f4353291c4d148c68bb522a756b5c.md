### Title
Division-by-Zero in `PerpEngine.socializeSubaccount` When `openInterest` Is Zero Permanently Blocks Subaccount Finalization — (File: `core/contracts/PerpEngine.sol`)

---

### Summary

In `PerpEngine.socializeSubaccount`, when a subaccount has a negative `vQuoteBalance` and the insurance fund is insufficient to cover it, the function attempts to divide by `state.openInterest` to compute a per-share funding adjustment. If `state.openInterest == 0` — a reachable state when all participants have closed their positions — this division reverts with `"DBZ"`, permanently blocking the finalization of the insolvent subaccount via `_finalizeSubaccount`.

---

### Finding Description

Inside `PerpEngine.socializeSubaccount`, after exhausting insurance coverage, the remaining negative `vQuoteBalance` is socialized across all open-interest holders:

```solidity
if (balance.vQuoteBalance < 0) {
    // socialize across all other participants
    int128 fundingPerShare = -balance.vQuoteBalance.div(
        state.openInterest   // ← reverts with "DBZ" if openInterest == 0
    );
``` [1](#0-0) 

There is no guard checking `state.openInterest != 0` before this division. The `openInterest` field tracks the sum of absolute position sizes across all subaccounts for a given product:

```solidity
state.openInterest -= balance.amount.abs(); // pre-update
// ...
state.openInterest += balance.amount;       // post-update (0 if closed)
``` [2](#0-1) 

When all participants close their positions, `openInterest` reaches exactly zero. A subaccount that closed its position retains its accumulated `vQuoteBalance` (funding payments, unrealized losses), which can be negative. This is a valid on-chain state because `_setBalanceAndUpdateBitmap` tracks balances where `amount == 0` but `vQuoteBalance != 0`:

```solidity
bool hasBalance = balance.amount != 0 || balance.vQuoteBalance != 0;
``` [3](#0-2) 

`socializeSubaccount` is called unconditionally inside `_finalizeSubaccount` in `ClearinghouseLiq.sol`:

```solidity
v.insurance = perpEngine.socializeSubaccount(
    txn.liquidatee,
    v.insurance
);
``` [4](#0-3) 

`_finalizeSubaccount` only requires `balance.amount == 0` for all perps — it does **not** require `vQuoteBalance == 0`:

```solidity
require(balance.amount == 0, ERR_NOT_FINALIZABLE_SUBACCOUNT);
``` [5](#0-4) 

So a subaccount with `amount == 0` and `vQuoteBalance < 0` passes the finalizability check, proceeds to `socializeSubaccount`, and triggers the division-by-zero revert.

---

### Impact Explanation

**Impact: High.**

`_finalizeSubaccount` is the terminal step of `liquidateSubaccountImpl`. When it reverts, the entire liquidation transaction reverts. The insolvent subaccount cannot be finalized by any liquidator, ever, as long as `openInterest == 0` for the affected product. The negative `vQuoteBalance` — representing real protocol losses — cannot be socialized or written off. The protocol's last-resort solvency mechanism is permanently broken for this subaccount, leaving an unresolvable bad-debt state.

---

### Likelihood Explanation

**Likelihood: Low.**

The trigger requires `openInterest == 0` for a product while a subaccount has `vQuoteBalance < 0`. This occurs naturally in:
- Low-activity or newly launched perp markets where a single user is the only participant
- End-of-lifecycle scenarios where all traders have exited a product
- A deliberate attacker who opens a position, accumulates negative vQuoteBalance via funding, closes the position, and ensures no other open interest remains

No privileged access is required. Any user can create this state by being the sole participant in a perp market.

---

### Recommendation

Add a zero-check on `state.openInterest` before the division in `PerpEngine.socializeSubaccount`. If `openInterest == 0`, there are no other participants to absorb the loss; the remaining negative `vQuoteBalance` should be absorbed entirely by the insurance fund or written off (zeroed out), consistent with the protocol's existing socialization intent:

```solidity
if (balance.vQuoteBalance < 0) {
    if (state.openInterest == 0) {
        // No open interest to socialize against; write off the remaining loss
        balance.vQuoteBalance = 0;
    } else {
        int128 fundingPerShare = -balance.vQuoteBalance.div(state.openInterest);
        state.cumulativeFundingLongX18 += fundingPerShare;
        state.cumulativeFundingShortX18 -= fundingPerShare;
        balance.vQuoteBalance = 0;
    }
}
```

---

### Proof of Concept

1. A perp product (e.g., `productId = 2`) is live with no other participants.
2. Attacker opens a long position: `balance.amount = X`, `openInterest = X`.
3. Funding payments accumulate: `balance.vQuoteBalance` becomes negative (e.g., `-1e18`).
4. Attacker closes the position: `balance.amount = 0`, `openInterest = 0`. `vQuoteBalance` remains `-1e18`.
5. Attacker's quote balance is also zero or negative (no collateral left).
6. Attacker's subaccount is under maintenance health (negative vQuoteBalance contributes negatively to health).
7. Liquidator calls `liquidateSubaccountImpl` with `txn.productId = type(uint32).max` to finalize.
8. `_finalizeSubaccount` passes the `balance.amount == 0` check.
9. `perpEngine.socializeSubaccount(txn.liquidatee, v.insurance)` is called.
10. Inside, `balance.vQuoteBalance = -1e18 < 0`; insurance is 0 or insufficient.
11. `fundingPerShare = -(-1e18).div(0)` → `MathSD21x18.div` reverts with `"DBZ"`.
12. The entire liquidation reverts. The subaccount is permanently un-finalizable. [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** core/contracts/PerpEngine.sol (L141-178)
```text
    function socializeSubaccount(bytes32 subaccount, int128 insurance)
        external
        returns (int128)
    {
        require(msg.sender == address(_clearinghouse), ERR_UNAUTHORIZED);

        uint32[] memory _productIds = getProductIds();
        for (uint128 i = 0; i < _productIds.length; ++i) {
            uint32 productId = _productIds[i];
            (State memory state, Balance memory balance) = getStateAndBalance(
                productId,
                subaccount
            );
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
                _setState(productId, state);
                _setBalanceAndUpdateBitmap(productId, subaccount, balance);
            }
        }
        return insurance;
    }
```

**File:** core/contracts/PerpEngineState.sol (L30-51)
```text
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
```

**File:** core/contracts/PerpEngineState.sol (L87-88)
```text
        bool hasBalance = balance.amount != 0 || balance.vQuoteBalance != 0;
        _setProductBit(subaccount, productId, hasBalance);
```

**File:** core/contracts/ClearinghouseLiq.sol (L279-413)
```text
    function _finalizeSubaccount(
        IEndpoint.LiquidateSubaccount calldata txn,
        ISpotEngine spotEngine,
        IPerpEngine perpEngine
    ) internal returns (bool) {
        if (txn.productId != type(uint32).max) {
            return false;
        }
        // check whether the subaccount can be finalized:
        // - all perps positions have closed
        // - all spread positions have closed
        // - all spot assets have closed
        // - all positive pnls have been settled

        FinalizeVars memory v;

        v.spotIds = spotEngine.getProductIds();
        v.perpIds = perpEngine.getProductIds();

        require(v.spotIds[0] == QUOTE_PRODUCT_ID);

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

        for (uint32 i = 0; i < v.perpIds.length; ++i) {
            uint32 perpId = v.perpIds[i];
            IPerpEngine.Balance memory balance = perpEngine.getBalance(
                perpId,
                txn.liquidatee
            );
            require(balance.amount == 0, ERR_NOT_FINALIZABLE_SUBACCOUNT);
        }

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

**File:** core/contracts/libraries/MathSD21x18.sol (L62-68)
```text
    function div(int128 x, int128 y) internal pure returns (int128) {
        unchecked {
            require(y != 0, ERR_DIV_BY_ZERO);
            int256 result = (int256(x) * ONE_X18) / y;
            require(result >= MIN_X18 && result <= MAX_X18, ERR_OVERFLOW);
            return int128(result);
        }
```
