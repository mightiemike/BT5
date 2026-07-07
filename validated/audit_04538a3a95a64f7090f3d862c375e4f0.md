### Title
Inflated `fundingPerShare` in `socializeSubaccount` Due to Division by Near-Zero `openInterest` — (File: `core/contracts/PerpEngine.sol`)

---

### Summary

`PerpEngine.socializeSubaccount` computes a per-unit loss accumulator by dividing the remaining negative `vQuoteBalance` by `state.openInterest`. When `openInterest` is zero or dust-sized, this produces a massively inflated `fundingPerShare` that permanently corrupts `cumulativeFundingLongX18` and `cumulativeFundingShortX18`, causing all existing position holders to absorb losses far exceeding the actual socialized amount — or causing an outright revert that bricks finalization.

---

### Finding Description

In `PerpEngine.socializeSubaccount`, after insurance is applied, any remaining negative `vQuoteBalance` is socialized across all open-interest holders:

```solidity
if (balance.vQuoteBalance < 0) {
    int128 fundingPerShare = -balance.vQuoteBalance.div(
        state.openInterest   // ← no zero-check
    );
    state.cumulativeFundingLongX18 += fundingPerShare;
    state.cumulativeFundingShortX18 -= fundingPerShare;
    balance.vQuoteBalance = 0;
}
``` [1](#0-0) 

`state.openInterest` at this point equals the total absolute position size of **all other participants** (the liquidated subaccount's own `balance.amount` is already 0, enforced by `_finalizeSubaccount`). There is no guard for `openInterest == 0` or for a dust-sized value.

By contrast, `PerpEngineState.updateStates` explicitly skips products with zero open interest:

```solidity
if (state.openInterest == 0) {
    continue;
}
``` [2](#0-1) 

The missing guard in `socializeSubaccount` is the root cause.

The corrupted accumulators are consumed in `_updateBalance` every time a position is updated:

```solidity
int128 diffX18 = cumulativeFundingAmountX18 - balance.lastCumulativeFundingX18;
int128 deltaQuote = vQuoteDelta - diffX18.mul(balance.amount);
balance.vQuoteBalance += deltaQuote;
``` [3](#0-2) 

Any participant who held a position **before** the socialization event will have `lastCumulativeFundingX18` set to the pre-inflation value. When they next interact with the engine, `diffX18` equals the full inflated `fundingPerShare`, and their `vQuoteBalance` is reduced by `fundingPerShare × position_size` — a value that can be orders of magnitude larger than the actual loss being socialized.

---

### Impact Explanation

**Two concrete impacts:**

1. **Revert / finalization DoS**: If `openInterest == 0` (the liquidated subaccount was the sole participant, or all others have closed), the `div(0)` reverts. `_finalizeSubaccount` cannot complete, the subaccount cannot be cleared, and the insurance fund cannot recover. The bad-debt subaccount is permanently stuck.

2. **Accounting corruption / asset loss**: If `openInterest` is dust-sized (e.g., one participant holds the minimum size increment), `fundingPerShare` is inflated by the ratio `|vQuoteBalance| / dust`. All existing position holders who close or update their positions after the event have their `vQuoteBalance` drained by `fundingPerShare × their_size`. The total loss extracted from honest participants far exceeds the actual bad debt being socialized, draining `availableSettle` and leaving the protocol insolvent for those users.

---

### Likelihood Explanation

`socializeSubaccount` is called from `_finalizeSubaccount` inside `liquidateSubaccountImpl`, which is reachable by any unprivileged liquidator submitting a `LiquidateSubaccount` transaction with `productId == type(uint32).max`. [4](#0-3) [5](#0-4) 

The condition is realistic in:
- A newly listed perp market with one or very few participants.
- A market where all other participants have closed positions before the finalization is submitted.
- A griefing scenario where an attacker deliberately holds the only remaining open position (minimum size) to maximize `fundingPerShare` inflation before triggering finalization of a bad-debt subaccount.

No admin keys, governance capture, or sequencer compromise are required.

---

### Recommendation

Add an explicit guard before the division, mirroring the existing pattern in `updateStates`:

```solidity
if (balance.vQuoteBalance < 0) {
    if (state.openInterest == 0) {
        // No participants to socialize against; absorb into insurance or leave as protocol loss
        state.availableSettle += balance.vQuoteBalance;
        balance.vQuoteBalance = 0;
    } else {
        int128 fundingPerShare = -balance.vQuoteBalance.div(state.openInterest);
        state.cumulativeFundingLongX18 += fundingPerShare;
        state.cumulativeFundingShortX18 -= fundingPerShare;
        balance.vQuoteBalance = 0;
    }
}
```

Additionally, consider a minimum `openInterest` threshold below which socialization is capped or redirected to the insurance fund, to prevent dust-denominator inflation.

---

### Proof of Concept

1. Deploy a perp market. UserA opens a long position of `1` (minimum size increment, e.g., `sizeIncrement = 1e15`).
2. UserB opens a long position of `1000e18` and then closes it, leaving only UserA's dust position. `openInterest = 1e15`.
3. UserC opens a position, accumulates a large negative `vQuoteBalance` (e.g., `-1000e18` USDC), and becomes undercollateralized. Insurance is insufficient.
4. Liquidator calls `LiquidateSubaccount` with `productId = type(uint32).max` to finalize UserC.
5. `_finalizeSubaccount` → `socializeSubaccount` is called.
6. `fundingPerShare = 1000e18 / 1e15 = 1000e18` per unit.
7. `cumulativeFundingLongX18 += 1000e18`.
8. UserA closes their `1e15` position: `deltaQuote = -1000e18 × 1e15 = -1e33` — a catastrophic loss far exceeding the actual bad debt.
9. Any subsequent long opener has `lastCumulativeFundingX18 = inflated value`; they are unaffected, but the protocol's `availableSettle` is now deeply negative, making it impossible for UserA to settle.

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

**File:** core/contracts/PerpEngineState.sol (L34-42)
```text
        int128 diffX18 = cumulativeFundingAmountX18 -
            balance.lastCumulativeFundingX18;
        int128 deltaQuote = vQuoteDelta - diffX18.mul(balance.amount);

        // apply delta
        balance.amount += balanceDelta;

        // apply vquote
        balance.vQuoteBalance += deltaQuote;
```

**File:** core/contracts/PerpEngineState.sol (L111-113)
```text
            if (state.openInterest == 0) {
                continue;
            }
```

**File:** core/contracts/ClearinghouseLiq.sol (L386-389)
```text
        v.insurance = perpEngine.socializeSubaccount(
            txn.liquidatee,
            v.insurance
        );
```

**File:** core/contracts/ClearinghouseLiq.sol (L598-603)
```text
    function liquidateSubaccountImpl(IEndpoint.LiquidateSubaccount calldata txn)
        external
    {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);
        require(txn.sender != txn.liquidatee, ERR_UNAUTHORIZED);
        require(isUnderMaintenance(txn.liquidatee), ERR_NOT_LIQUIDATABLE);
```
