### Title
Division by Zero in `PerpEngine.socializeSubaccount` When `openInterest` Is Zero Blocks Liquidation — (File: `core/contracts/PerpEngine.sol`)

---

### Summary

`PerpEngine.socializeSubaccount` divides by `state.openInterest` without a zero guard. The same contract's `updateStates` explicitly skips products with zero `openInterest`, but `socializeSubaccount` does not. If a subaccount holds a negative `vQuoteBalance` on a product whose `openInterest` has reached zero, the socialization call reverts, permanently blocking the clearinghouse from completing liquidation of that subaccount.

---

### Finding Description

In `PerpEngine.socializeSubaccount`, after insurance is applied, any remaining negative `vQuoteBalance` is spread across all open-interest holders via:

```solidity
int128 fundingPerShare = -balance.vQuoteBalance.div(
    state.openInterest
);
```

`MathSD21x18.div` enforces `require(y != 0, ERR_DIV_BY_ZERO)`. [1](#0-0) 

The division at line 166–168 of `PerpEngine.sol` has no guard against `state.openInterest == 0`. [2](#0-1) 

By contrast, `PerpEngineState.updateStates` explicitly skips products with zero open interest before performing any per-share arithmetic:

```solidity
if (state.openInterest == 0) {
    continue;
}
``` [3](#0-2) 

The inconsistency is the root cause: the protocol acknowledges that `openInterest` can be zero in one code path but fails to guard the other.

---

### Impact Explanation

When `socializeSubaccount` reverts, the clearinghouse cannot complete the liquidation of the insolvent subaccount. The subaccount's negative `vQuoteBalance` cannot be written off, and the protocol is left in a state where:

- The insolvent subaccount cannot be fully liquidated.
- The insurance fund cannot be reconciled.
- Any subsequent call to `socializeSubaccount` for the same subaccount/product will continue to revert.

This is a **solvency/accounting corruption** impact: the protocol's loss-mutualization mechanism is permanently disabled for the affected product, trapping the clearinghouse in an unresolvable state. [4](#0-3) 

---

### Likelihood Explanation

The trigger state — `openInterest == 0` with a subaccount holding `vQuoteBalance < 0` — is reachable through normal trading:

1. A trader opens a perp position (openInterest increases).
2. The trader closes the position at a loss (openInterest decrements back to zero if they were the sole participant, but `vQuoteBalance` remains negative until settled).
3. The trader's collateral falls below maintenance margin before settlement.
4. The liquidation path calls `socializeSubaccount`.

The condition is more likely on low-liquidity or newly listed perp products where a single participant can represent the entire open interest. [5](#0-4) 

---

### Recommendation

Add a zero check for `state.openInterest` before the division in `socializeSubaccount`, mirroring the guard already present in `updateStates`:

```solidity
if (balance.vQuoteBalance < 0) {
    if (state.openInterest == 0) {
        // No open interest to socialize against; absorb via insurance or skip.
        balance.vQuoteBalance = 0;
    } else {
        int128 fundingPerShare = -balance.vQuoteBalance.div(state.openInterest);
        state.cumulativeFundingLongX18 += fundingPerShare;
        state.cumulativeFundingShortX18 -= fundingPerShare;
        balance.vQuoteBalance = 0;
    }
}
```

The exact handling when `openInterest == 0` (absorb into insurance, skip, or revert with a descriptive error) should be decided based on protocol invariants, but the division must not be reached unconditionally. [3](#0-2) 

---

### Proof of Concept

1. Product X is a newly listed perp with no open interest (`state.openInterest == 0`).
2. Alice opens a long position on product X — `openInterest` increases to `N`.
3. Alice closes her position at a loss — `openInterest` decrements back to `0`; Alice's `vQuoteBalance` is `-L` (negative).
4. Alice's collateral falls below maintenance margin.
5. A liquidator triggers the liquidation path, which calls `ClearinghouseLiq` → `PerpEngine.socializeSubaccount(alice, insurance)`.
6. Insurance covers part of the loss but `balance.vQuoteBalance` remains `< 0`.
7. Execution reaches `fundingPerShare = -balance.vQuoteBalance.div(state.openInterest)` with `state.openInterest == 0`.
8. `MathSD21x18.div` reverts with `"DBZ"`.
9. The liquidation transaction reverts. Alice's subaccount cannot be socialized. The clearinghouse is stuck. [6](#0-5) [1](#0-0)

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

**File:** core/contracts/PerpEngineState.sol (L111-113)
```text
            if (state.openInterest == 0) {
                continue;
            }
```
