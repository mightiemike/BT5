### Title
Division-by-Zero in `PerpEngine.socializeSubaccount` Permanently Blocks Liquidation Finalization When `openInterest` Is Zero — (`core/contracts/PerpEngine.sol`)

---

### Summary

`PerpEngine.socializeSubaccount` unconditionally divides by `state.openInterest` when distributing residual losses across market participants. If `state.openInterest == 0` at the time of the call — a reachable condition when the liquidatee is the sole remaining participant in a perp market — the transaction reverts with a division-by-zero error, permanently blocking the finalization of the insolvent subaccount.

---

### Finding Description

In `PerpEngine.socializeSubaccount`, when the insurance fund cannot fully cover a subaccount's negative `vQuoteBalance`, the protocol attempts to spread the remaining loss across all open-interest holders:

```solidity
if (balance.vQuoteBalance < 0) {
    // socialize across all other participants
    int128 fundingPerShare = -balance.vQuoteBalance.div(
        state.openInterest
    );
```

`MathSD21x18.div` explicitly reverts with `"DBZ"` when the divisor is zero:

```solidity
function div(int128 x, int128 y) internal pure returns (int128) {
    unchecked {
        require(y != 0, ERR_DIV_BY_ZERO);
```

`state.openInterest` tracks the sum of absolute position sizes across all participants. When a subaccount's perp position is fully closed (`amount == 0`), its contribution to `openInterest` is removed. If the liquidatee was the only open-interest holder in the market, `state.openInterest` will be `0` by the time `socializeSubaccount` is called.

This is not a hypothetical edge case. `_finalizeSubaccount` in `ClearinghouseLiq.sol` explicitly requires that all of the liquidatee's perp positions have `amount == 0` before calling `socializeSubaccount`:

```solidity
for (uint32 i = 0; i < v.perpIds.length; ++i) {
    uint32 perpId = v.perpIds[i];
    IPerpEngine.Balance memory balance = perpEngine.getBalance(perpId, txn.liquidatee);
    require(balance.amount == 0, ERR_NOT_FINALIZABLE_SUBACCOUNT);
}
```

After this check passes, `perpEngine.socializeSubaccount(txn.liquidatee, v.insurance)` is called. At this point, if the liquidatee was the only participant, `state.openInterest == 0`, and the division reverts.

Notably, the protocol already handles this case correctly in `PerpEngineState.updateStates`:

```solidity
if (state.openInterest == 0) {
    continue;
}
```

The same guard is absent in `socializeSubaccount`.

---

### Impact Explanation

When the revert occurs, the entire `liquidateSubaccountImpl` call fails. The insolvent subaccount cannot be finalized:

- The subaccount's negative `vQuoteBalance` is never cleared.
- The insurance fund cannot recover the losses.
- The loss socialization mechanism is permanently broken for that market/subaccount combination.
- No liquidator can ever complete the finalization, leaving the protocol holding an unresolvable bad debt.

This is a direct financial impact beyond pure DoS: the insurance fund is unable to account for and close out the insolvent position, corrupting the protocol's solvency accounting.

---

### Likelihood Explanation

The condition is reachable in any perp market where the liquidatee is the sole remaining open-interest holder. This is realistic in:

- Newly launched perp markets with low participation.
- Markets where all other participants have closed their positions.
- A deliberate griefing attack: a user opens a position in a new market, ensures they are the only participant, and allows their position to become liquidatable. The liquidation finalization will revert indefinitely.

The attacker-controlled entry path is: open a perp position → be the only participant → become liquidatable → any call to `liquidateSubaccountImpl` with `productId == type(uint32).max` (triggering `_finalizeSubaccount`) will revert.

---

### Recommendation

Add the same zero-guard that already exists in `updateStates` to `socializeSubaccount`:

```solidity
if (balance.vQuoteBalance < 0) {
    int128 insuranceCover = MathHelper.min(insurance, -balance.vQuoteBalance);
    insurance -= insuranceCover;
    balance.vQuoteBalance += insuranceCover;
    state.availableSettle += insuranceCover;

    if (balance.vQuoteBalance < 0) {
+       if (state.openInterest == 0) {
+           // No other participants; absorb remaining loss into insurance or write off
+           balance.vQuoteBalance = 0;
+       } else {
            int128 fundingPerShare = -balance.vQuoteBalance.div(state.openInterest);
            state.cumulativeFundingLongX18 += fundingPerShare;
            state.cumulativeFundingShortX18 -= fundingPerShare;
            balance.vQuoteBalance = 0;
+       }
    }
```

---

### Proof of Concept

1. Deploy the protocol with a new perp market (productId = P).
2. User A opens a long position of size S in market P. `state.openInterest = S`.
3. No other users open positions in market P.
4. User A's collateral drops below maintenance margin (e.g., via price movement).
5. User A's position is closed through prior liquidation steps. `state.openInterest = 0`.
6. User A still has `vQuoteBalance < 0` (realized loss exceeds insurance coverage).
7. Liquidator calls `liquidateSubaccountImpl` with `productId = type(uint32).max` to finalize.
8. `_finalizeSubaccount` passes the `amount == 0` check (position already closed).
9. `perpEngine.socializeSubaccount(txn.liquidatee, v.insurance)` is called.
10. Inside `socializeSubaccount`, `balance.vQuoteBalance < 0` and `state.openInterest == 0`.
11. `(-balance.vQuoteBalance).div(0)` triggers `require(y != 0, "DBZ")` → revert.
12. The finalization is permanently blocked; the bad debt is never resolved.

---

**Relevant code references:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** core/contracts/PerpEngineState.sol (L111-113)
```text
            if (state.openInterest == 0) {
                continue;
            }
```
