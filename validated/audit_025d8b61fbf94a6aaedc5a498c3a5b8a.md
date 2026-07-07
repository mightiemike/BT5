### Title
Division-by-Zero in `PerpEngine.socializeSubaccount` When `openInterest == 0` Permanently Blocks Subaccount Finalization — (`core/contracts/PerpEngine.sol`)

---

### Summary

`PerpEngine.socializeSubaccount` unconditionally divides by `state.openInterest` when a subaccount still has a negative `vQuoteBalance` after insurance coverage. `MathSD21x18.div` enforces `require(y != 0, "DBZ")`, so if `openInterest == 0` the call reverts. Because `ClearinghouseLiq._finalizeSubaccount` **requires** all perp positions to be closed (`balance.amount == 0`) before calling `socializeSubaccount`, the finalization path itself creates the exact precondition for the revert: a product with no open interest but a subaccount carrying residual negative `vQuoteBalance`.

---

### Finding Description

**Root cause — `MathSD21x18.div` with zero divisor:**

`MathSD21x18.div` explicitly reverts on a zero denominator: [1](#0-0) 

The division in `socializeSubaccount` passes `state.openInterest` as the denominator with no prior zero-guard: [2](#0-1) 

**How `openInterest` and `vQuoteBalance` can diverge:**

`openInterest` tracks only `abs(balance.amount)` — the sum of open position sizes across all subaccounts. It is updated in `_updateBalance` independently of `vQuoteBalance`: [3](#0-2) 

A subaccount that closes its position (`amount → 0`) retains whatever `vQuoteBalance` it accumulated. If that balance is negative (a losing trade), `openInterest` drops to zero while `vQuoteBalance < 0` persists.

**The finalization path enforces the dangerous precondition:**

`_finalizeSubaccount` explicitly requires `balance.amount == 0` for every perp product before proceeding: [4](#0-3) 

It then calls `perpEngine.socializeSubaccount`: [5](#0-4) 

If no other subaccounts hold open positions in that product, `state.openInterest == 0` at this point, and the division reverts.

---

### Impact Explanation

The revert propagates up through `_finalizeSubaccount` → `liquidateSubaccountImpl`. The insolvent subaccount can never be finalized: every attempt reverts. Insurance funds allocated to cover the loss are permanently locked in the contract and cannot be redistributed. The protocol's socialization mechanism — the last-resort solvency backstop — is rendered inoperable for any product that reaches zero open interest while a subaccount still carries negative `vQuoteBalance`.

---

### Likelihood Explanation

The scenario is reachable through ordinary protocol usage:

1. Subaccount A opens a long in product X, accumulates losses (`vQuoteBalance < 0`).
2. Subaccount A closes the position (`amount = 0`); `vQuoteBalance` remains negative.
3. No other subaccounts hold open positions in product X → `openInterest == 0`.
4. Subaccount A's health falls below maintenance margin (negative `vQuoteBalance` reduces health).
5. A liquidator submits `LiquidateSubaccount` with `productId == type(uint32).max` to trigger finalization.
6. `_finalizeSubaccount` verifies `balance.amount == 0` ✓, exhausts insurance, then calls `socializeSubaccount`.
7. `socializeSubaccount` hits `div(state.openInterest)` with `openInterest == 0` → revert "DBZ".

No privileged access, no oracle manipulation, and no exotic token behavior is required. It is a natural end-state for any low-liquidity or newly-launched perp product.

---

### Recommendation

Add a zero-check before the division. If `openInterest == 0` there are no other participants to socialize the loss against; the loss should be absorbed entirely by the insurance fund or written off:

```solidity
if (balance.vQuoteBalance < 0) {
    if (state.openInterest == 0) {
        // No open interest to socialize against; loss is unrecoverable here
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

```solidity
// 1. Deploy PerpEngine + Clearinghouse in a Hardhat test.
// 2. Register product X with openInterest = 0.
// 3. Give subaccountA a stored balance of:
//      balance.amount = 0  (position closed)
//      balance.vQuoteBalance = -100e18  (residual loss)
// 4. Confirm states[productX].openInterest == 0.
// 5. Call liquidateSubaccountImpl with productId = type(uint32).max
//    targeting subaccountA (health < 0 due to negative vQuoteBalance).
// 6. Observe revert with reason "DBZ" from MathSD21x18.div.
// 7. Assert the call never succeeds regardless of insurance amount.
```

The state in step 3 is reachable via `updateBalance(productX, subaccountA, -amount, pnl)` to close the position, leaving `vQuoteBalance` negative. No special privileges are needed beyond normal trading access.

### Citations

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

**File:** core/contracts/PerpEngine.sol (L164-171)
```text
                if (balance.vQuoteBalance < 0) {
                    // socialize across all other participants
                    int128 fundingPerShare = -balance.vQuoteBalance.div(
                        state.openInterest
                    );
                    state.cumulativeFundingLongX18 += fundingPerShare;
                    state.cumulativeFundingShortX18 -= fundingPerShare;
                    balance.vQuoteBalance = 0;
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

**File:** core/contracts/ClearinghouseLiq.sol (L386-389)
```text
        v.insurance = perpEngine.socializeSubaccount(
            txn.liquidatee,
            v.insurance
        );
```
