### Title
Division-by-Zero in `socializeSubaccount` When `openInterest == 0` Permanently Blocks Subaccount Finalization — (`core/contracts/PerpEngine.sol`)

---

### Summary

`PerpEngine.socializeSubaccount` divides by `state.openInterest` without a zero-guard. When all positions in a product are closed (`openInterest == 0`) but a subaccount still carries a negative `vQuoteBalance` that insurance cannot fully cover, the `MathSD21x18.div` call reverts with `"DBZ"`, permanently blocking finalization of that subaccount and locking the insurance balance in `ClearinghouseLiq`.

---

### Finding Description

In `PerpEngine.socializeSubaccount`, after applying whatever insurance is available, the remaining negative `vQuoteBalance` is socialized across open-interest holders: [1](#0-0) 

```solidity
if (balance.vQuoteBalance < 0) {
    int128 fundingPerShare = -balance.vQuoteBalance.div(
        state.openInterest          // ← reverts if == 0
    );
```

`MathSD21x18.div` contains an explicit guard: [2](#0-1) 

```solidity
function div(int128 x, int128 y) internal pure returns (int128) {
    unchecked {
        require(y != 0, ERR_DIV_BY_ZERO);   // reverts "DBZ"
```

`state.openInterest` is the **global** sum of absolute position sizes across all subaccounts for that product, maintained in `_updateBalance`: [3](#0-2) 

It equals zero when every subaccount in the market has `balance.amount == 0`. A subaccount can simultaneously have `balance.amount == 0` (position closed) and `vQuoteBalance < 0` (closed at a loss). This is the exact state required for finalization: `_finalizeSubaccount` enforces `balance.amount == 0` for every perp before calling `socializeSubaccount`: [4](#0-3) 

The call to `socializeSubaccount` is then unconditional: [5](#0-4) 

The protocol itself already recognizes that `openInterest == 0` requires special handling — `updateStates` skips the product entirely in that case: [6](#0-5) 

No equivalent guard exists in `socializeSubaccount`.

---

### Impact Explanation

- **Permanent finalization DoS**: `liquidateSubaccountImpl` with `productId == type(uint32).max` always reverts for the affected subaccount; no alternative finalization path exists.
- **Insurance funds locked**: `v.insurance` in `_finalizeSubaccount` is assigned from the return value of `socializeSubaccount`, which never returns. The `insurance` storage variable is never updated, freezing those funds.
- **Bad debt accumulates**: The negative `vQuoteBalance` is never cleared, and the subaccount's liabilities remain unresolved indefinitely.

---

### Likelihood Explanation

The precondition — `openInterest == 0` with a subaccount holding `vQuoteBalance < 0` — is reachable in any low-liquidity market or during an orderly wind-down where all participants close positions. It does not require any privileged access or exotic token behavior. The liquidatee's position must already be closed (`balance.amount == 0`) to pass the finalization pre-check, which is the normal end-state of a liquidation sequence.

---

### Recommendation

Add a zero-check on `state.openInterest` before the division. If `openInterest == 0` there are no holders to socialize to; the loss should be absorbed entirely by insurance or written off:

```solidity
if (balance.vQuoteBalance < 0) {
    if (state.openInterest == 0) {
        // No open positions to socialize against; write off residual loss
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

1. Deploy the protocol on a local Hardhat fork.
2. Create a single subaccount, open a perp position in product `P`, then close it at a loss so that `balance.amount == 0` and `balance.vQuoteBalance == -1e18`.
3. Ensure no other subaccount holds an open position in `P` so `states[P].openInterest == 0`.
4. Drain or zero the insurance fund.
5. Drive the subaccount's maintenance health below zero (e.g., via oracle price move).
6. Call `liquidateSubaccountImpl` with `productId == type(uint32).max`.
7. Observe the transaction reverts with `"DBZ"` from `MathSD21x18.div`.
8. Repeat indefinitely — the subaccount can never be finalized.

### Citations

**File:** core/contracts/PerpEngine.sol (L164-168)
```text
                if (balance.vQuoteBalance < 0) {
                    // socialize across all other participants
                    int128 fundingPerShare = -balance.vQuoteBalance.div(
                        state.openInterest
                    );
```

**File:** core/contracts/libraries/MathSD21x18.sol (L62-65)
```text
    function div(int128 x, int128 y) internal pure returns (int128) {
        unchecked {
            require(y != 0, ERR_DIV_BY_ZERO);
            int256 result = (int256(x) * ONE_X18) / y;
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

**File:** core/contracts/PerpEngineState.sol (L111-113)
```text
            if (state.openInterest == 0) {
                continue;
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
