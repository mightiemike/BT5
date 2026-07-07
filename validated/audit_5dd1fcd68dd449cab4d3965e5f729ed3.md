### Title
Division by Zero in `PerpEngine.socializeSubaccount()` When `openInterest` Is Zero Blocks Liquidation Finalization — (File: `core/contracts/PerpEngine.sol`)

---

### Summary

`PerpEngine.socializeSubaccount()` divides by `state.openInterest` without a zero-guard. When the liquidatee is the sole remaining participant in a perp market and their position has already been closed prior to finalization, `openInterest` is zero, causing an unconditional revert that permanently blocks liquidation finalization for that subaccount.

---

### Finding Description

In `PerpEngine.socializeSubaccount()`, after exhausting insurance coverage, the protocol attempts to spread the remaining negative `vQuoteBalance` across all open-interest holders via a per-share funding adjustment:

```solidity
if (balance.vQuoteBalance < 0) {
    // socialize across all other participants
    int128 fundingPerShare = -balance.vQuoteBalance.div(
        state.openInterest   // <-- no zero-check
    );
``` [1](#0-0) 

`MathSD21x18.div()` enforces `require(z != 0, ERR_DIV_BY_ZERO)`: [2](#0-1) 

`state.openInterest` is the sum of absolute position sizes across all participants. It is decremented when any position is closed: [3](#0-2) 

`_finalizeSubaccount` in `ClearinghouseLiq.sol` requires that **all** of the liquidatee's perp positions are already closed (`balance.amount == 0`) before calling `perpEngine.socializeSubaccount`: [4](#0-3) [5](#0-4) 

If the liquidatee was the **only** participant in a given perp market, closing their position reduces `openInterest` to zero. When `socializeSubaccount` then encounters a negative `vQuoteBalance` for that product, the division by zero reverts unconditionally.

---

### Impact Explanation

Liquidation finalization for the affected subaccount is permanently blocked. The bad debt cannot be socialized, insurance cannot be applied, and the subaccount cannot be cleared. This corrupts the protocol's ability to recover from insolvent positions and leaves the bad debt unresolved on-chain.

**Impact: Medium** — the bad debt is not stolen but is permanently unresolvable through the normal liquidation path, degrading protocol solvency accounting.

---

### Likelihood Explanation

A perp market with a single active participant is a realistic early-stage or low-liquidity scenario. The liquidatee's position is closed by the liquidator in prior liquidation steps before `_finalizeSubaccount` is reached, making `openInterest == 0` at the exact moment socialization is attempted. No privileged access or external manipulation is required.

**Likelihood: Medium** — requires the liquidatee to be the sole open-interest holder in at least one perp product at finalization time.

---

### Recommendation

Add a zero-check for `state.openInterest` before the division in `PerpEngine.socializeSubaccount()`. If `openInterest` is zero, there are no other participants to socialize against; the loss should be absorbed entirely by the insurance fund or written off without a per-share adjustment:

```solidity
if (balance.vQuoteBalance < 0) {
    if (state.openInterest == 0) {
        // No open interest to socialize against; write off the loss
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

1. Deploy the protocol with a single perp product (e.g., `productId = 2`).
2. Subaccount A opens a long position; no other account participates. `openInterest = |A.amount|`.
3. Funding payments accumulate, making `A.vQuoteBalance < 0`.
4. A closes their position (`A.amount = 0`). `openInterest` is decremented to `0`.
5. A's health falls below maintenance threshold.
6. Liquidator calls `liquidateSubaccountImpl` with `productId = type(uint32).max` to trigger `_finalizeSubaccount`.
7. `_finalizeSubaccount` confirms `A.amount == 0` for all perps, then calls `perpEngine.socializeSubaccount(A, insurance)`.
8. Inside `socializeSubaccount`, `balance.vQuoteBalance < 0` and `state.openInterest == 0`.
9. `MathSD21x18.div` reverts with `"DBZ"`.
10. The entire liquidation transaction reverts; the subaccount can never be finalized. [6](#0-5)

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

**File:** core/contracts/libraries/MathSD21x18.sol (L19-30)
```text
    function mulDiv(
        int128 x,
        int128 y,
        int128 z
    ) internal pure returns (int128) {
        unchecked {
            require(z != 0, ERR_DIV_BY_ZERO);
            int256 result = (int256(x) * y) / z;
            require(result >= MIN_X18 && result <= MAX_X18, ERR_OVERFLOW);
            return int128(result);
        }
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
