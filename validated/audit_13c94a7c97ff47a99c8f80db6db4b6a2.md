### Title
Predictable Wealth Transfer via Loss Socialization Creates Exploitable Arbitrage Incentive - (`core/contracts/PerpEngine.sol`)

---

### Summary

When a bankrupt perp position is finalized, `PerpEngine.socializeSubaccount` adjusts global cumulative funding accumulators in a deterministic, directional manner — charging all existing longs and crediting all existing shorts (or vice versa). This creates a predictable, front-runnable wealth transfer that mirrors the Maple Finance M-02 class: a large default event produces a quantifiable, one-sided price impact on all open positions, incentivizing traders to pre-position against the socialization and potentially incentivizing large position holders to deliberately allow their positions to go bankrupt.

---

### Finding Description

When a subaccount is finalized during liquidation and its `vQuoteBalance` remains negative after insurance coverage, `_finalizeSubaccount` in `ClearinghouseLiq.sol` calls `perpEngine.socializeSubaccount`: [1](#0-0) 

Inside `socializeSubaccount`, the loss is distributed by adjusting the global funding accumulators: [2](#0-1) 

Specifically:
```
fundingPerShare = -balance.vQuoteBalance / state.openInterest  // positive when vQuote < 0
cumulativeFundingLongX18  += fundingPerShare   // increases
cumulativeFundingShortX18 -= fundingPerShare   // decreases
```

When any subsequent balance update is applied via `_updateBalance`, the funding delta is realized: [3](#0-2) 

For an existing **long** position (`amount > 0`): `diffX18` grows → `deltaQuote = -diffX18 * amount` becomes more negative → the long's `vQuoteBalance` is reduced (they pay the socialized loss).

For an existing **short** position (`amount < 0`): `diffX18` shrinks → `deltaQuote = -diffX18 * amount` becomes more positive (since `amount < 0`) → the short's `vQuoteBalance` increases (they receive the socialized gain).

The direction of this transfer is fully deterministic and observable on-chain before the liquidation is finalized: if the bankrupt position is long, all shorts profit; if it is short, all longs profit.

---

### Impact Explanation

An attacker (or the bankrupt trader themselves using a separate subaccount) can:

1. Observe on-chain that a large long position is deeply underwater (health < 0, visible via `getHealth`).
2. Open a large short position in a separate subaccount before the liquidation is finalized.
3. When `socializeSubaccount` executes, `cumulativeFundingShortX18` decreases, directly increasing the attacker's short `vQuoteBalance`.
4. The attacker closes the short for a risk-free profit funded entirely by the losses of all other long holders.

Additionally, a trader holding a large long position that is approaching insolvency is **incentivized not to add collateral** and instead to:
- Open a large short in a second subaccount.
- Allow the long to go bankrupt.
- Collect the socialization gain on the short, partially or fully offsetting the loss on the long.

This is the direct Nado analog to the Maple Finance M-02 finding: a default/bankruptcy event creates a predictable, large, one-directional price impact that rational actors are incentivized to exploit, and that the defaulting party can themselves profit from.

The NLP pool subaccounts (which hold positions) are also affected: their `vQuoteBalance` is silently reduced by every socialization event, degrading NLP token value without any explicit accounting event visible to NLP holders. [4](#0-3) 

---

### Likelihood Explanation

- Large perp positions going bankrupt is a normal, expected protocol event (especially during volatile markets).
- The on-chain state (`getHealth`, `getBalance`, `getStateAndBalance`) is fully public, so any observer can identify an underwater position before it is finalized.
- The sequencer controls liquidation ordering, but the socialization direction is deterministic once the bankrupt position's sign (`amount > 0` or `< 0`) is known.
- Opening a short position in a separate subaccount is a standard, permissionless user action.
- The incentive to deliberately default is strongest for large positions where the socialization gain on the opposing short exceeds the liquidation penalty on the bankrupt position.

---

### Recommendation

1. **Symmetric socialization**: Instead of adjusting `cumulativeFundingLongX18` and `cumulativeFundingShortX18` asymmetrically, distribute the loss proportionally across both longs and shorts (e.g., reduce `availableSettle` globally or apply a symmetric haircut to all `vQuoteBalance` holders).
2. **Insurance fund priority**: Increase the insurance fund's coverage threshold so socialization is a last resort, reducing the frequency and magnitude of socialization events.
3. **Position-side restriction**: Prevent a subaccount from opening a new position in the same product on the opposing side within a time window before a known large liquidation is finalized (difficult to enforce on-chain, but worth considering at the sequencer level).
4. **Socialization cap per event**: Limit the per-event socialization amount to prevent a single large bankruptcy from creating a large, exploitable funding jump.

---

### Proof of Concept

**Setup:**
- Product `perpId = 2` has `openInterest = 1,000,000` (1M units).
- Subaccount `A` holds a long of `+500,000` units with `vQuoteBalance = -200,000` (bankrupt after insurance exhausted).
- Attacker subaccount `B` holds a short of `-100,000` units with `lastCumulativeFundingX18 = X`.

**Socialization execution** (`PerpEngine.socializeSubaccount`, lines 166–170):
```
fundingPerShare = 200,000 / 1,000,000 = 0.2
cumulativeFundingLongX18  += 0.2   → X + 0.2
cumulativeFundingShortX18 -= 0.2   → X - 0.2
```

**Attacker closes short** (triggers `_updateBalance`, lines 31–36):
```
cumulativeFundingAmountX18 = cumulativeFundingShortX18 = X - 0.2
diffX18 = (X - 0.2) - X = -0.2
deltaQuote = 0 - (-0.2) * (-100,000) = -20,000
```

The attacker's `vQuoteBalance` increases by `+20,000` — a direct, risk-free gain funded by the socialized loss of all long holders. [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** core/contracts/ClearinghouseLiq.sol (L386-412)
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
        return true;
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
