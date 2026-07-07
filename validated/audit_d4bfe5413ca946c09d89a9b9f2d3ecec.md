### Title
Division-by-Zero in `socializeSubaccount` When `openInterest` Is Zero Causes Liquidation DOS — (`core/contracts/PerpEngine.sol`)

---

### Summary

`PerpEngine.socializeSubaccount` unconditionally divides by `state.openInterest` when attempting to spread a subaccount's residual negative `vQuoteBalance` across all open-interest holders. When `openInterest` is zero — a reachable state when all positions in a product have been closed — the `MathSD21x18.div` call reverts with `"DBZ"`, permanently blocking the liquidation of any subaccount that holds a negative `vQuoteBalance` in that product.

---

### Finding Description

In `PerpEngine.socializeSubaccount`, after insurance is exhausted, the protocol attempts to distribute the remaining loss across all open-interest holders:

```solidity
// PerpEngine.sol lines 164–171
if (balance.vQuoteBalance < 0) {
    // socialize across all other participants
    int128 fundingPerShare = -balance.vQuoteBalance.div(
        state.openInterest   // <-- reverts if == 0
    );
    state.cumulativeFundingLongX18 += fundingPerShare;
    state.cumulativeFundingShortX18 -= fundingPerShare;
    balance.vQuoteBalance = 0;
}
``` [1](#0-0) 

`MathSD21x18.div` enforces a hard revert on a zero denominator:

```solidity
// MathSD21x18.sol line 64
require(y != 0, ERR_DIV_BY_ZERO);
``` [2](#0-1) 

`state.openInterest` is the sum of `|balance.amount|` across all subaccounts for a given product. It is zero whenever every subaccount has closed its position (`amount == 0`). A subaccount can simultaneously hold `amount == 0` and `vQuoteBalance < 0` — for example, after closing a losing position without settling the realized loss. In that state the subaccount's health is negative (the negative `vQuoteBalance` reduces health), making it a valid liquidation target.

The function does not guard against `openInterest == 0` before dividing, unlike `updateStates`, which explicitly skips the funding calculation when `openInterest == 0`:

```solidity
// PerpEngineState.sol lines 111–113
if (state.openInterest == 0) {
    continue;
}
``` [3](#0-2) 

No equivalent guard exists in `socializeSubaccount`.

---

### Impact Explanation

When the revert fires, the entire `liquidateSubaccount` transaction (which `delegatecall`s into `ClearinghouseLiq` and ultimately calls `socializeSubaccount`) reverts. The unhealthy subaccount cannot be liquidated. Its negative `vQuoteBalance` remains, the protocol cannot recover the bad debt, and the liquidation mechanism — a core safety invariant — is rendered inoperable for that product until open interest is non-zero again. This is a direct DOS on the liquidation path, analogous to the PoolTogether claimer being unable to process any claim when the last-tier prize size is zero.

---

### Likelihood Explanation

The condition is reachable in normal operation:

1. A perp product experiences low activity; all traders close their positions (`openInterest → 0`).
2. One trader closed a position at a loss; their `vQuoteBalance` is negative and `amount == 0`.
3. Their spot collateral is insufficient to cover the loss, making health negative.
4. The sequencer submits a `LiquidateSubaccount` transaction.
5. `socializeSubaccount` is called, hits `div(0)`, and reverts.

This is especially likely in newly listed or low-liquidity perp markets, or during market wind-downs.

---

### Recommendation

Add a guard before the division, mirroring the pattern already used in `updateStates`:

```solidity
if (balance.vQuoteBalance < 0) {
    if (state.openInterest == 0) {
        // No participants to socialize to; absorb loss into protocol reserve
        // or revert with a descriptive error rather than a DBZ panic.
        balance.vQuoteBalance = 0;
    } else {
        int128 fundingPerShare = -balance.vQuoteBalance.div(state.openInterest);
        state.cumulativeFundingLongX18 += fundingPerShare;
        state.cumulativeFundingShortX18 -= fundingPerShare;
        balance.vQuoteBalance = 0;
    }
}
```

The exact recovery strategy (absorb into insurance, skip, or revert with a meaningful error) should be a deliberate protocol decision, but the unconditional division must be guarded.

---

### Proof of Concept

1. Deploy the protocol with one perp product.
2. Subaccount A opens a long position; subaccount B opens a short position.
3. Funding payments cause subaccount A to accumulate a negative `vQuoteBalance`.
4. Both A and B close their positions → `openInterest == 0`.
5. Subaccount A now has `amount == 0`, `vQuoteBalance < 0`; health is negative.
6. Sequencer submits `LiquidateSubaccount` for A.
7. `Clearinghouse.liquidateSubaccount` → `delegatecall` to `ClearinghouseLiq` → `PerpEngine.socializeSubaccount`.
8. Inside `socializeSubaccount`: `balance.vQuoteBalance < 0` is true, insurance is zero, so the division `(-balance.vQuoteBalance).div(0)` is reached.
9. `MathSD21x18.div` reverts with `"DBZ"`.
10. The liquidation transaction reverts; subaccount A remains unliquidated indefinitely.

### Citations

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

**File:** core/contracts/PerpEngineState.sol (L111-113)
```text
            if (state.openInterest == 0) {
                continue;
            }
```
