Now I have all the information needed. Let me trace the exact code path carefully.

### Title
Stale `lastLiquidationFees` Causes Unnecessary Socialization and Insurance Fund Overstatement During Finalization — (`core/contracts/ClearinghouseLiq.sol`)

---

### Summary

`lastLiquidationFees` is a persistent storage variable that is **never reset to zero** after a finalization. When `_finalizeSubaccount` is called for a subaccount that had no preceding `_handleLiquidationPayment` call in the same liquidation sequence, the stale value from a prior subaccount's liquidation is subtracted from insurance before socialization and then unconditionally added back afterward. This causes `socializeSubaccount` to receive less insurance than is actually available, spreading unnecessary losses to other participants, while the insurance fund is left overstated by the stale fee amount — phantom value not backed by real tokens.

---

### Finding Description

**Storage variable:**
`lastLiquidationFees` is declared in `ClearinghouseStorage` and is only ever written in one place: [1](#0-0) [2](#0-1) 

It is **never reset to zero** anywhere in the codebase.

**The finalization pattern in `_finalizeSubaccount`:** [3](#0-2) 

The design intent (comment at lines 581–585) is that `lastLiquidationFees` represents fees from the **most recent liquidation step for the same subaccount** — fees that were just added to `insurance` and should be withheld from socialization to avoid blocking it. The add-back at line 410 restores them after socialization.

**The bug:** `_finalizeSubaccount` is entered whenever `txn.productId == type(uint32).max`: [4](#0-3) 

A liquidator can call `liquidateSubaccountImpl` with `productId == type(uint32).max` for a subaccount that is directly finalizable (zero open positions, only negative `vQuoteBalance`) **without any preceding `_handleLiquidationPayment` call for that subaccount**. In this case, `lastLiquidationFees` still holds the value from the last liquidation of a completely different subaccount.

**`socializeSubaccount` in `PerpEngine`:** [5](#0-4) 

It consumes insurance to cover negative `vQuoteBalance`, and only socializes the remainder across open-interest holders. If it receives `insurance - X` (stale X subtracted) instead of `insurance`, it socializes `X` more than necessary.

---

### Impact Explanation

Concrete trace with numbers:

| Step | Action | `insurance` | `lastLiquidationFees` |
|------|--------|-------------|----------------------|
| 0 | Initial | 100 | 0 |
| 1 | Liquidate subaccount A, fees=10 | 110 | **10** |
| 2 | Finalize subaccount A (deficit=50) | 60 | **10** (not reset) |
| 3 | Finalize subaccount B directly (deficit=55) | **10** (wrong) | 10 |

In step 3:
- `v.insurance = 60 − 10 = 50` (stale subtraction)
- `socializeSubaccount(B, 50)`: covers 50, remaining deficit = 5 → **socializes 5 to other participants**
- `v.insurance = 0 + 10 = 10` → `insurance = 10`

**Correct behavior** (if `lastLiquidationFees = 0`):
- `socializeSubaccount(B, 60)`: covers 55, returns 5 → **no socialization**
- `insurance = 5`

**Result of the bug:**
1. Other participants absorb 5 in unnecessary socialization losses (value incorrectly transferred from participants to the insurance fund accounting).
2. `insurance = 10` but should be `5` — the fund is **overstated by 5** (phantom value not backed by real tokens). When this phantom insurance is later used to cover a future deficit via `spotEngine.updateBalance(QUOTE_PRODUCT_ID, liquidatee, amount)`, it inflates the spot engine's total quote balance without corresponding token backing, leaving the protocol undercollateralized.

---

### Likelihood Explanation

The scenario is realistic and requires no special privileges:
- Any subaccount with only negative perp `vQuoteBalance` and no open positions is directly finalizable.
- A liquidator simply calls `liquidateSubaccountImpl` with `productId == type(uint32).max` for such a subaccount after any other subaccount has been liquidated (leaving `lastLiquidationFees` non-zero).
- The `isUnderMaintenance` check at line 603 is the only gate, and a subaccount with a large negative `vQuoteBalance` and no assets will be under maintenance. [6](#0-5) 

---

### Recommendation

Reset `lastLiquidationFees` to zero at the start of `_finalizeSubaccount` (or at the end, after the add-back), so that a finalization with no preceding liquidation step for the same subaccount does not inherit stale fees:

```solidity
// At the start of _finalizeSubaccount, after the productId check:
int128 _lastFees = lastLiquidationFees;
lastLiquidationFees = 0; // reset so it doesn't bleed into next finalization

v.insurance = insurance;
v.insurance -= _lastFees;
// ...
v.insurance += _lastFees;
insurance = v.insurance;
```

Alternatively, scope `lastLiquidationFees` to the specific liquidatee being processed (e.g., a `mapping(bytes32 => int128)`) so stale values from other subaccounts cannot interfere.

---

### Proof of Concept

```solidity
// 1. Liquidate subaccountA for productId=2, generating fees=10
//    → insurance = 110, lastLiquidationFees = 10

// 2. Finalize subaccountA (productId = type(uint32).max)
//    → _finalizeSubaccount: v.insurance = 110-10=100, socialize(A,100), +10 back
//    → insurance = 60, lastLiquidationFees = 10 (NOT RESET)

// 3. SubaccountB has: amount=0 on all perps, vQuoteBalance=-55, no spot assets
//    → isUnderMaintenance(B) = true (large negative vQuote)
//    → Liquidator calls liquidateSubaccountImpl(B, productId=type(uint32).max)
//    → _finalizeSubaccount: v.insurance = 60-10=50 (stale!)
//    → socializeSubaccount(B, 50): covers 50, socializes 5 to all OI holders
//    → v.insurance = 0+10=10, insurance=10

// Assert: insurance == 10 (should be 5)
// Assert: 5 was socialized to other participants (should be 0)
// The 5 difference is phantom insurance — not backed by tokens.
```

### Citations

**File:** core/contracts/ClearinghouseStorage.sol (L25-25)
```text
    int128 internal lastLiquidationFees;
```

**File:** core/contracts/ClearinghouseLiq.sol (L284-286)
```text
        if (txn.productId != type(uint32).max) {
            return false;
        }
```

**File:** core/contracts/ClearinghouseLiq.sol (L368-411)
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
