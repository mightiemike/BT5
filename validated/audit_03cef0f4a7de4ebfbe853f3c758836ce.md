## Analysis

Let me trace the exact execution path and state conditions.

**Step 1: `_finalizeSubaccount` precondition check**

`_finalizeSubaccount` requires `balance.amount == 0` for every perp product before calling `socializeSubaccount`. [1](#0-0) 

**Step 2: How `openInterest` is maintained**

In `_updateBalance`, `openInterest` tracks the sum of absolute values of all open positions globally. When a position is closed to zero, its contribution is removed: [2](#0-1) 

If the liquidatee was the last open-interest holder in a product and their position was closed (via prior liquidation steps), `states[productId].openInterest` becomes `0`. The liquidatee's `vQuoteBalance` is **not** zeroed by closing the position — it retains the accumulated unrealized loss.

**Step 3: `getStateAndBalance` in `socializeSubaccount`**

When `socializeSubaccount` calls `getStateAndBalance`, it calls `_updateBalance(state, balance, 0, 0)`. Since `balance.amount == 0`, the pre-update subtracts `0` from `openInterest`, so the memory `state.openInterest` equals the stored global value — which is `0`. [3](#0-2) 

**Step 4: The division by zero**

`socializeSubaccount` reaches line 166 when `balance.vQuoteBalance < 0` after insurance coverage. With `state.openInterest == 0`, `MathSD21x18.div` explicitly reverts: [4](#0-3) [5](#0-4) 

**Step 5: No guard exists**

There is no `if (state.openInterest == 0)` guard before the division. The `updateStates` function skips funding when `openInterest == 0`, but `socializeSubaccount` has no equivalent guard. [6](#0-5) 

**Step 6: Negative PnL settlement before `socializeSubaccount` does not prevent this**

`_finalizeSubaccount` attempts to settle negative vQuoteBalance using the liquidatee's quote balance first, but only if `quoteBalance.amount > 0`. If the quote balance is already zero or negative (the typical underwater scenario), this loop is a no-op, and `socializeSubaccount` is called with the remaining negative `vQuoteBalance` intact. [7](#0-6) 

---

### Title
Division-by-Zero in `PerpEngine.socializeSubaccount` Permanently Blocks Finalization When `openInterest == 0` — (`core/contracts/PerpEngine.sol`)

### Summary
When the last open-interest holder in a perp product closes their position before `_finalizeSubaccount` is called, `state.openInterest` becomes `0`. If that subaccount still carries a negative `vQuoteBalance` not fully covered by insurance, `socializeSubaccount` unconditionally divides by `state.openInterest`, reverting with `ERR_DIV_BY_ZERO` and permanently blocking finalization.

### Finding Description
In `PerpEngine.socializeSubaccount` (line 166), the loss-socialization path computes:

```solidity
int128 fundingPerShare = -balance.vQuoteBalance.div(state.openInterest);
```

`state.openInterest` is the global sum of absolute position sizes for the product. It is entirely possible for this to be `0` while a subaccount still holds a negative `vQuoteBalance` — specifically when the subaccount was the last open-interest holder and its position was closed (reducing `openInterest` to `0`) before finalization is triggered. The `MathSD21x18.div` function enforces `require(y != 0, ERR_DIV_BY_ZERO)`, causing an unconditional revert. No guard exists in `socializeSubaccount` for this case, unlike `updateStates` which explicitly skips when `openInterest == 0`.

### Impact Explanation
`_finalizeSubaccount` becomes permanently non-executable for any subaccount that satisfies this state. The insurance fund cannot be recovered or redistributed. The protocol invariant — that any underwater subaccount can always be finalized — is broken. This is a Critical solvency/accounting failure: the protocol is left with an irrecoverable bad debt and a locked insurance fund.

### Likelihood Explanation
The scenario is reachable through the normal liquidation path with no special privileges:
1. A subaccount holds the only open perp position in a product and goes underwater.
2. A liquidator closes the perp position (setting `balance.amount = 0`, leaving `vQuoteBalance < 0`).
3. No other participant holds a position in that product, so `openInterest = 0`.
4. The liquidator (or anyone) calls `liquidateSubaccount` with `productId = type(uint32).max`.
5. `_finalizeSubaccount` → `socializeSubaccount` → division by zero → permanent revert.

This is especially likely for low-liquidity or newly-listed perp products.

### Recommendation
Add a guard in `socializeSubaccount` before the division. If `state.openInterest == 0` and `balance.vQuoteBalance < 0` after insurance, the loss cannot be socialized (there are no other participants to absorb it). The correct behavior is to absorb the remaining loss directly from the insurance fund or write it off:

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

### Proof of Concept
```
1. Deploy protocol with one perp product (productId = 2).
2. SubaccountA opens a long position of size X at price P.
   → states[2].openInterest = X
   → balances[2][A].amount = X, balances[2][A].vQuoteBalance = -P*X
3. Price drops to P' < P. SubaccountA is underwater.
4. Liquidator calls liquidateSubaccount(A, productId=2, amount=-X):
   → balances[2][A].amount = 0
   → balances[2][A].vQuoteBalance = -P*X + P'*X = -(P-P')*X  (still negative)
   → states[2].openInterest = 0  (A was the only holder)
5. insurance < (P-P')*X  (insurance insufficient to cover the loss)
6. Liquidator calls liquidateSubaccount(A, productId=type(uint32).max):
   → _finalizeSubaccount is entered
   → balance.amount == 0 ✓ (passes the check at line 319)
   → quoteBalance.amount <= 0 (A is underwater, no quote to settle with)
   → perpEngine.socializeSubaccount(A, insurance) is called
   → balance.vQuoteBalance < 0 → enters outer if
   → insuranceCover < -balance.vQuoteBalance → enters inner if
   → -balance.vQuoteBalance.div(0) → REVERT ERR_DIV_BY_ZERO
7. Assert: the call at step 6 always reverts. The subaccount can never be finalized.
```

### Citations

**File:** core/contracts/ClearinghouseLiq.sol (L313-320)
```text
        for (uint32 i = 0; i < v.perpIds.length; ++i) {
            uint32 perpId = v.perpIds[i];
            IPerpEngine.Balance memory balance = perpEngine.getBalance(
                perpId,
                txn.liquidatee
            );
            require(balance.amount == 0, ERR_NOT_FINALIZABLE_SUBACCOUNT);
        }
```

**File:** core/contracts/ClearinghouseLiq.sol (L346-366)
```text
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
```

**File:** core/contracts/PerpEngineState.sol (L29-51)
```text
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
```

**File:** core/contracts/PerpEngineState.sol (L54-63)
```text
    function getStateAndBalance(uint32 productId, bytes32 subaccount)
        public
        view
        returns (State memory, Balance memory)
    {
        State memory state = states[productId];
        Balance memory balance = balances[productId][subaccount];
        _updateBalance(state, balance, 0, 0);
        return (state, balance);
    }
```

**File:** core/contracts/PerpEngineState.sol (L111-113)
```text
            if (state.openInterest == 0) {
                continue;
            }
```

**File:** core/contracts/PerpEngine.sol (L164-172)
```text
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
