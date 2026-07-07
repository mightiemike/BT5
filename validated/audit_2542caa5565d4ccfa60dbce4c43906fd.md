### Title
Asymmetric Funding Fee Accumulation Due to OI Imbalance Creates Protocol Deficit — (File: `core/contracts/PerpEngineState.sol`)

---

### Summary

In `PerpEngineState.updateStates()`, both `cumulativeFundingLongX18` and `cumulativeFundingShortX18` are incremented by the **same per-unit** `paymentAmount`. When long open interest (OI) ≠ short OI, the total funding paid by one side does not equal the total funding received by the other side. The net difference is never credited or debited to `availableSettle` or any other protocol accounting variable, creating an untracked deficit whenever the receiving side's aggregate OI exceeds the paying side's aggregate OI.

---

### Finding Description

`updateStates` computes a single `paymentAmount` and applies it symmetrically to both cumulative funding accumulators:

```solidity
// PerpEngineState.sol lines 130-132
int128 paymentAmount = priceDiffX18.mul(dtX18).div(ONE_DAY_X18);
state.cumulativeFundingLongX18 += paymentAmount;
state.cumulativeFundingShortX18 += paymentAmount;
``` [1](#0-0) 

When `_updateBalance` is later called for any position, the realized funding for that position is:

```solidity
// PerpEngineState.sol lines 34-36
int128 diffX18 = cumulativeFundingAmountX18 - balance.lastCumulativeFundingX18;
int128 deltaQuote = vQuoteDelta - diffX18.mul(balance.amount);
``` [2](#0-1) 

For a long (`amount > 0`): `deltaQuote = -paymentAmount × amount` (longs pay).
For a short (`amount < 0`): `deltaQuote = -paymentAmount × amount = +paymentAmount × |amount|` (shorts receive).

Therefore:
- **Total paid by all longs** = `paymentAmount × longOI`
- **Total received by all shorts** = `paymentAmount × shortOI`

These are equal only when `longOI == shortOI`. When they differ, the net imbalance `paymentAmount × (shortOI − longOI)` is never reconciled against `availableSettle`. The `availableSettle` field is only ever modified inside `socializeSubaccount` (insurance cover path) and `settlePnl` (deduction path):

```solidity
// PerpEngine.sol lines 94-95
state.availableSettle -= canSettle;
balance.vQuoteBalance -= canSettle;
``` [3](#0-2) 

There is no corresponding credit to `availableSettle` when the receiving side accumulates more `vQuoteBalance` than the paying side contributed. The inflated `vQuoteBalance` flows directly into health calculations via `_getBalance`:

```solidity
// PerpEngineState.sol lines 99-100
Balance memory balance = getBalance(productId, subaccount);
return (balance.amount, balance.vQuoteBalance);
``` [4](#0-3) 

A trader on the receiving side therefore presents a health score that is better than the protocol can actually support, allowing them to open positions whose eventual settlement will exceed the real assets backing `availableSettle`.

---

### Impact Explanation

When `shortOI > longOI` and `paymentAmount > 0` (mark > index, longs pay shorts), shorts collectively receive `paymentAmount × (shortOI − longOI)` more `vQuoteBalance` than longs contributed. This excess is phantom value: it improves shorts' health scores, enabling them to open additional positions. When those positions are settled via `settlePnl`, the protocol must honor the inflated `vQuoteBalance` claims against an `availableSettle` pool that was never funded for the excess, resulting in a direct protocol deficit. The same logic applies symmetrically when `longOI > shortOI` and `paymentAmount < 0`.

The deficit compounds over time with every `updateStates` call in an OI-imbalanced market, which is a routine market condition.

---

### Likelihood Explanation

OI imbalance (`longOI ≠ shortOI`) is a normal, persistent market condition in any perpetual market. The funding mechanism exists precisely because OI is typically imbalanced. Every `updateStates` call in such a state silently widens the accounting gap. No special attacker action is required; any trader on the receiving side passively accumulates the inflated `vQuoteBalance` and can exploit it by opening new positions.

---

### Recommendation

After computing `paymentAmount`, calculate the net funding imbalance and adjust `availableSettle` accordingly:

```solidity
// Pseudocode
int128 longFundingTotal = paymentAmount.mul(longOI);
int128 shortFundingTotal = paymentAmount.mul(shortOI);
int128 netImbalance = shortFundingTotal - longFundingTotal; // positive = shorts received more
state.availableSettle -= netImbalance; // debit the pool for the excess given to shorts
```

Alternatively, scale the per-unit `paymentAmount` for the receiving side so that total paid equals total received (i.e., use OI-weighted rates), matching the recommendation in the referenced external report.

---

### Proof of Concept

**Setup:**
- `longOI = 100`, `shortOI = 200`
- `priceDiffX18 > 0` (mark > index), after cap: `paymentAmount = 0.01`

**`updateStates` execution:**
```
cumulativeFundingLongX18  += 0.01
cumulativeFundingShortX18 += 0.01
``` [5](#0-4) 

**Funding realized on next `_updateBalance`:**
- All longs pay: `0.01 × 100 = 1.0` (deducted from their `vQuoteBalance`)
- All shorts receive: `0.01 × 200 = 2.0` (added to their `vQuoteBalance`)

**Net imbalance:** shorts received `1.0` more than longs paid. This `1.0` is phantom `vQuoteBalance` — it was never deposited into `availableSettle`.

**Exploit path:** A short-side trader observes their improved health score (from inflated `vQuoteBalance`) and opens a new position sized to the phantom margin. When they later call `settlePnl`, `getSettlementState` returns:

```solidity
availableSettle = MathHelper.min(calculatePositionPnl(balance, productId), state.availableSettle);
``` [6](#0-5) 

The `state.availableSettle` was never credited for the `1.0` excess, so the settlement is underfunded. Repeated over many funding intervals and across multiple traders, this creates a growing protocol deficit and eventual insolvency.

### Citations

**File:** core/contracts/PerpEngineState.sol (L34-36)
```text
        int128 diffX18 = cumulativeFundingAmountX18 -
            balance.lastCumulativeFundingX18;
        int128 deltaQuote = vQuoteDelta - diffX18.mul(balance.amount);
```

**File:** core/contracts/PerpEngineState.sol (L99-100)
```text
        Balance memory balance = getBalance(productId, subaccount);
        return (balance.amount, balance.vQuoteBalance);
```

**File:** core/contracts/PerpEngineState.sol (L130-132)
```text
                int128 paymentAmount = priceDiffX18.mul(dtX18).div(ONE_DAY_X18);
                state.cumulativeFundingLongX18 += paymentAmount;
                state.cumulativeFundingShortX18 += paymentAmount;
```

**File:** core/contracts/PerpEngine.sol (L94-95)
```text
                state.availableSettle -= canSettle;
                balance.vQuoteBalance -= canSettle;
```

**File:** core/contracts/PerpEngine.sol (L135-138)
```text
        availableSettle = MathHelper.min(
            calculatePositionPnl(balance, productId),
            state.availableSettle
        );
```
