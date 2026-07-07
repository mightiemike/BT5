### Title
Incorrect Social Loss Side Attribution in Perp Socialization — (`File: core/contracts/PerpEngine.sol`)

---

### Summary

In `PerpEngine.socializeSubaccount`, when a bankrupt subaccount's residual negative `vQuoteBalance` is socialized after the insurance fund is exhausted, the loss is spread symmetrically to **both** longs and shorts by incrementing `cumulativeFundingLongX18` and decrementing `cumulativeFundingShortX18` simultaneously. The correct behavior is to charge only the **opposite** side of the bankrupt position — the counterparties who hold the corresponding profit. This is a direct analog to the H01 bug class: social losses attributed to the wrong side.

---

### Finding Description

The socialization path in `PerpEngine.socializeSubaccount` is reached from `ClearinghouseLiq._finalizeSubaccount` when the insurance fund cannot fully cover a bankrupt subaccount's negative perp `vQuoteBalance`. The relevant code:

```solidity
// PerpEngine.sol lines 164–171
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

`state.openInterest` is the **total** open interest — the sum of absolute position sizes for both longs and shorts, as maintained in `_updateBalance`:

```solidity
// PerpEngineState.sol lines 30, 46, 49
state.openInterest -= balance.amount.abs();
// ...
state.openInterest += balance.amount;       // for longs
state.openInterest -= balance.amount;       // for shorts (subtracting negative)
```

The effect of the two cumulative funding mutations on each side, derived from `_updateBalance`'s PnL formula (`deltaQuote = vQuoteDelta - diffX18.mul(balance.amount)`):

| Side | Mutation | `diffX18` | `deltaQuote` | Net effect |
|---|---|---|---|---|
| Long (`amount > 0`) | `cumulativeFundingLongX18 += fundingPerShare` | `+fundingPerShare` | `−fundingPerShare × amount < 0` | Long loses money |
| Short (`amount < 0`) | `cumulativeFundingShortX18 -= fundingPerShare` | `−fundingPerShare` | `−(−fundingPerShare) × (negative) < 0` | Short loses money |

Both sides are charged. The total charged equals the loss (`fundingPerShare × openInterest = −balance.vQuoteBalance`), so the system stays solvent in aggregate, but the **distribution is wrong**: the side that was on the same side as the bankrupt position is charged a portion of the loss it should not bear. Only the counterparty side (the opposite side) should absorb the socialized loss, because they hold the corresponding unrealized profit that the bankrupt position cannot pay out.

The `_finalizeSubaccount` function enforces `balance.amount == 0` for all perp positions before calling `socializeSubaccount` (line 319), meaning the original direction of the bankrupt position is no longer present in state at the time of socialization. The protocol therefore has no mechanism to restrict the charge to the correct side, and the current code defaults to charging both. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

---

### Impact Explanation

When socialization fires, every long position in the affected perp market has its effective `vQuoteBalance` reduced by `fundingPerShare × |longAmount|`, and every short position has its `vQuoteBalance` reduced by `fundingPerShare × |shortAmount|`. The side that was on the **same side** as the bankrupt position is charged a share of a loss it did not cause and has no obligation to cover. Their settled or settleable PnL is permanently reduced. The opposite side is undercharged relative to the correct amount. In markets with highly skewed open interest (e.g., 90% longs, 10% shorts), the majority of the socialized loss falls on the same side as the bankrupt position, which is the maximum-impact scenario. The accounting corruption is permanent and irreversible once the state is written. [5](#0-4) [6](#0-5) 

---

### Likelihood Explanation

This path is triggered whenever: (1) a subaccount is finalized via `liquidateSubaccountImpl` with `txn.productId == type(uint32).max`, (2) the liquidatee has a residual negative `vQuoteBalance` in at least one perp product after all PnL settlement steps, and (3) the insurance fund is insufficient to cover the full deficit. Condition (3) is the only gating condition that requires a specific protocol state; conditions (1) and (2) are normal outcomes of any deep liquidation. Any liquidator can trigger this path by submitting a finalization transaction when the insurance fund is low or empty, which is precisely the scenario where socialization is most likely to occur. No privileged access is required. [7](#0-6) [8](#0-7) 

---

### Recommendation

Track long and short open interest separately in `IPerpEngine.State` (e.g., `openInterestLong` and `openInterestShort`). Before the bankrupt subaccount's position is zeroed out during the finalization flow, record its direction. In `socializeSubaccount`, pass the original position direction and charge only the opposite side:

- If the bankrupt position was **long**: `cumulativeFundingShortX18 -= loss / openInterestShort`
- If the bankrupt position was **short**: `cumulativeFundingLongX18 += loss / openInterestLong`

This ensures that only the counterparties — who hold the corresponding unrealized profit — absorb the socialized loss, consistent with the zero-sum invariant of a perpetual futures market. [9](#0-8) [10](#0-9) 

---

### Proof of Concept

**Setup:** A perp market with two participants:
- Alice: long 100 units, `lastCumulativeFundingX18 = 0`
- Bob: short 100 units, `lastCumulativeFundingX18 = 0`
- `openInterest = 200`, `cumulativeFundingLongX18 = 0`, `cumulativeFundingShortX18 = 0`

A third subaccount (Carol) was long 50 units, went bankrupt, was fully liquidated (`balance.amount = 0`), and has `vQuoteBalance = -1000` (residual loss). Insurance is empty.

**Current behavior (`socializeSubaccount` called):**
```
fundingPerShare = 1000 / 200 = 5
cumulativeFundingLongX18  += 5  → 5
cumulativeFundingShortX18 -= 5  → -5
```

When Alice's balance is next read:
- `diffX18 = 5 - 0 = 5`
- `deltaQuote = -5 × 100 = -500` → Alice loses 500 (same side as Carol, unfairly charged)

When Bob's balance is next read:
- `diffX18 = -5 - 0 = -5`
- `deltaQuote = -(-5) × (-100) = -500` → Bob loses 500 (opposite side, undercharged — should lose 1000)

**Correct behavior:** Carol was long, so only shorts (Bob) should cover the loss:
```
fundingPerShare_correct = 1000 / 100 = 10
cumulativeFundingShortX18 -= 10  → -10
```
Alice: unchanged. Bob: `deltaQuote = -(-10) × (-100) = -1000` → Bob covers the full loss. [11](#0-10) [12](#0-11)

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

**File:** core/contracts/ClearinghouseLiq.sol (L313-319)
```text
        for (uint32 i = 0; i < v.perpIds.length; ++i) {
            uint32 perpId = v.perpIds[i];
            IPerpEngine.Balance memory balance = perpEngine.getBalance(
                perpId,
                txn.liquidatee
            );
            require(balance.amount == 0, ERR_NOT_FINALIZABLE_SUBACCOUNT);
```

**File:** core/contracts/ClearinghouseLiq.sol (L368-389)
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

**File:** core/contracts/interfaces/engine/IPerpEngine.sol (L15-20)
```text
    struct State {
        int128 cumulativeFundingLongX18;
        int128 cumulativeFundingShortX18;
        int128 availableSettle;
        int128 openInterest;
    }
```
