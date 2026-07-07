### Title
Division by Zero in `PerpEngine.socializeSubaccount()` When `openInterest` Is Zero — (File: `core/contracts/PerpEngine.sol`)

---

### Summary

`PerpEngine.socializeSubaccount()` performs an unchecked division by `state.openInterest` when spreading a subaccount's residual negative `vQuoteBalance` across all participants. If `openInterest` is zero at the time of socialization, `MathSD21x18.div` reverts with `ERR_DIV_BY_ZERO`, blocking the entire liquidation finalization path. The protocol even defines `ERR_NO_OPEN_INTEREST` in `Errors.sol` but never guards this division with it.

---

### Finding Description

In `PerpEngine.socializeSubaccount()`, after insurance is exhausted, the remaining negative `vQuoteBalance` is spread across all open-interest holders via a per-share funding adjustment:

```solidity
if (balance.vQuoteBalance < 0) {
    // socialize across all other participants
    int128 fundingPerShare = -balance.vQuoteBalance.div(
        state.openInterest   // ← reverts if openInterest == 0
    );
```

`MathSD21x18.div` unconditionally requires `y != 0`:

```solidity
function div(int128 x, int128 y) internal pure returns (int128) {
    unchecked {
        require(y != 0, ERR_DIV_BY_ZERO);
```

The reachable scenario:

1. A subaccount opens a perp position and accumulates negative funding payments (`vQuoteBalance < 0`).
2. The subaccount closes its position (`amount = 0`), but the accumulated `vQuoteBalance` debt persists.
3. All other participants in that market also close their positions, driving `state.openInterest` to zero.
4. The subaccount's quote balance falls below zero (e.g., from further funding or losses), making it under-maintenance.
5. A liquidator submits a finalization transaction (`productId == type(uint32).max`).
6. `_finalizeSubaccount` → `perpEngine.socializeSubaccount` → division by zero → revert.

The protocol defines `ERR_NO_OPEN_INTEREST = "NOI"` in `Errors.sol` but never uses it to guard this division path, confirming the check was anticipated but omitted.

---

### Impact Explanation

`_finalizeSubaccount` in `ClearinghouseLiq.sol` calls `perpEngine.socializeSubaccount` unconditionally:

```solidity
v.insurance = perpEngine.socializeSubaccount(
    txn.liquidatee,
    v.insurance
);
```

A revert here causes the entire `liquidateSubaccountImpl` call to revert. The underwater subaccount cannot be finalized. Its bad debt is never resolved: the insurance fund cannot cover it, the spot socialization path (`spotEngine.socializeSubaccount`) is never reached, and the isolated subaccount close path is never triggered. The protocol is left with an irresolvable insolvent subaccount, corrupting the accounting invariant that all bad debt must eventually be socialized or covered by insurance.

**Severity:** Low–Medium. The corrupted state is accounting/solvency (bad debt permanently stuck), not direct fund theft.
**Impact:** Medium — unresolvable bad debt, broken finalization invariant.
**Likelihood:** Low — requires a market where all positions are closed but one subaccount retains a negative `vQuoteBalance`, which is an edge case but a natural protocol state during market wind-down or low-activity periods.

---

### Likelihood Explanation

The condition (`openInterest == 0` with `vQuoteBalance < 0`) arises naturally:

- A subaccount opens a perp, accumulates negative funding, closes the position (amount → 0, vQuoteBalance stays negative).
- The market goes to zero open interest (all other traders exit).
- The subaccount's quote balance deteriorates further (e.g., from spot losses), triggering under-maintenance health.
- Any liquidator can now trigger the revert by submitting a finalization.

No privileged access, no oracle manipulation, and no sequencer compromise is required. The liquidator is a standard unprivileged caller.

---

### Recommendation

Add a zero-check on `state.openInterest` before the division in `socializeSubaccount`. If `openInterest` is zero, there are no other participants to socialize against; the residual `vQuoteBalance` should be zeroed out and absorbed by the insurance fund or written off:

```solidity
if (balance.vQuoteBalance < 0) {
    if (state.openInterest == 0) {
        // No open interest to socialize against; absorb residual debt
        balance.vQuoteBalance = 0;
    } else {
        int128 fundingPerShare = -balance.vQuoteBalance.div(
            state.openInterest
        );
        state.cumulativeFundingLongX18 += fundingPerShare;
        state.cumulativeFundingShortX18 -= fundingPerShare;
        balance.vQuoteBalance = 0;
    }
}
```

The already-defined `ERR_NO_OPEN_INTEREST` constant should be used if a revert is preferred over silent absorption.

---

### Proof of Concept

**State setup:**
- Product `perpId = 2` (even, so it is a perp product).
- Subaccount `A` opens a long, accumulates −500 USDC in funding (`vQuoteBalance = -500e18`), then closes the position (`amount = 0`). `openInterest` for `perpId` drops to 0 as all other traders also exit.
- Subaccount `A`'s quote balance falls to −100 USDC (spot losses), making it under-maintenance.

**Attack path:**
1. Any liquidator calls `Endpoint.submitTransactions` with a `LiquidateSubaccount` transaction where `productId = type(uint32).max` (finalization mode) and `liquidatee = A`.
2. `Clearinghouse.liquidateSubaccount` → `delegatecall` → `ClearinghouseLiq.liquidateSubaccountImpl`.
3. `isUnderMaintenance(A)` → true. `_finalizeSubaccount` is entered.
4. All perp `amount` balances are 0, all positive PnL settled, quote balance still negative.
5. `perpEngine.socializeSubaccount(A, insurance)` is called.
6. For `perpId = 2`: `balance.vQuoteBalance = -500e18 < 0`, insurance insufficient → enters the `if (balance.vQuoteBalance < 0)` branch.
7. `MathSD21x18.div(-500e18, 0)` → `require(0 != 0)` → **revert("DIV0")**.
8. The entire finalization reverts. Subaccount `A` remains permanently insolvent and unfinalizable.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** core/contracts/PerpEngine.sol (L163-171)
```text
                // actually socialize if still not enough
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

**File:** core/contracts/ClearinghouseLiq.sol (L386-390)
```text
        v.insurance = perpEngine.socializeSubaccount(
            txn.liquidatee,
            v.insurance
        );

```

**File:** core/contracts/common/Errors.sol (L55-56)
```text
// Socializing product with no open interest
string constant ERR_NO_OPEN_INTEREST = "NOI";
```
