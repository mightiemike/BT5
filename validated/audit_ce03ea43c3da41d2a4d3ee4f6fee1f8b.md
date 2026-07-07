### Title
Division by Zero in `socializeSubaccount` When `openInterest` Is Zero Permanently Blocks Subaccount Finalization — (`core/contracts/PerpEngine.sol`)

---

### Summary

`PerpEngine.socializeSubaccount` computes `fundingPerShare` by dividing by `state.openInterest` **before** checking whether `state.openInterest` is zero. When a perp market has no remaining open interest at the time of subaccount finalization, this causes an unconditional revert, permanently blocking the liquidation finalization path and leaving the insolvent subaccount in a stuck state.

---

### Finding Description

In `PerpEngine.socializeSubaccount`, when insurance is insufficient to cover a liquidatee's negative `vQuoteBalance`, the protocol attempts to spread the loss across all remaining open-interest holders:

```solidity
// actually socialize if still not enough
if (balance.vQuoteBalance < 0) {
    // socialize across all other participants
    int128 fundingPerShare = -balance.vQuoteBalance.div(
        state.openInterest          // <-- division before zero-check
    );
    state.cumulativeFundingLongX18 += fundingPerShare;
    state.cumulativeFundingShortX18 -= fundingPerShare;
    balance.vQuoteBalance = 0;
}
``` [1](#0-0) 

`MathSD21x18.div` unconditionally reverts with `"DBZ"` when the denominator is zero: [2](#0-1) 

`state.openInterest` tracks the sum of absolute position sizes across all participants. It is decremented when positions are closed via `_updateBalance`: [3](#0-2) 

`socializeSubaccount` is called exclusively from `_finalizeSubaccount` in `ClearinghouseLiq`, **after** the liquidatee's perp `amount` has already been forced to zero (enforced by the `require` at line 319): [4](#0-3) [5](#0-4) 

Because the liquidatee's `amount` is already `0` by the time `socializeSubaccount` is called, their contribution to `openInterest` has already been removed. If no other participants hold open positions in that perp market, `state.openInterest == 0`, and the division reverts unconditionally — even though the correct semantic is simply "no one to socialize to."

---

### Impact Explanation

The revert inside `socializeSubaccount` propagates up through `_finalizeSubaccount` → `liquidateSubaccountImpl` → `Clearinghouse.liquidateSubaccount` (via `delegatecall`). The entire liquidation finalization transaction reverts. Because the only path to finalize an insolvent subaccount is through this code, the subaccount is permanently stuck:

- Its negative `vQuoteBalance` can never be socialized or cleared.
- The subaccount can never be finalized.
- Any collateral or accounting state tied to the subaccount is frozen.

This is a direct analog to the Cork Protocol bug: a critical settlement/cleanup function is permanently bricked by a division-by-zero that occurs before the zero-guard, caused by a reachable protocol state (all open interest drained from a market).

---

### Likelihood Explanation

The condition `state.openInterest == 0` at finalization time is reachable through normal protocol usage:

1. A perp market is thinly traded or reaches end-of-life; all participants except the liquidatee close their positions.
2. The liquidatee's own position is closed during the liquidation steps (reducing their `amount` to 0), but they retain a negative `vQuoteBalance` (realized loss from the closed position).
3. A liquidator submits a finalization transaction (`productId == type(uint32).max`).
4. `socializeSubaccount` is called with `state.openInterest == 0` → revert.

No privileged access, governance capture, or external oracle manipulation is required. Any liquidator can trigger this path.

---

### Recommendation

Move the zero-check guard **before** the division, mirroring the fix pattern from the reference report:

```solidity
if (balance.vQuoteBalance < 0) {
    if (state.openInterest == 0) {
        // No remaining participants to socialize to;
        // loss is unrecoverable — zero out the balance.
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

This matches the existing pattern in `PerpEngineState.updateStates`, which already guards against zero `openInterest` before processing: [6](#0-5) 

---

### Proof of Concept

**Setup:**
- Perp market with product ID `P` has exactly one participant: Alice (the future liquidatee).
- Alice opens a long position of size `S`, so `state.openInterest = S`.

**Steps:**
1. Alice's position loses value. She becomes under-maintenance health.
2. A liquidator calls `liquidateSubaccountImpl` with `productId = P` (not `type(uint32).max`) to close Alice's position. After this, `balance.amount = 0`, `state.openInterest = 0`, but `balance.vQuoteBalance < 0` (realized loss).
3. Insurance is insufficient to cover `balance.vQuoteBalance`.
4. Liquidator calls `liquidateSubaccountImpl` with `productId = type(uint32).max` to finalize.
5. `_finalizeSubaccount` passes the `balance.amount == 0` check (line 319).
6. `perpEngine.socializeSubaccount(txn.liquidatee, v.insurance)` is called.
7. Inside `socializeSubaccount`: `balance.vQuoteBalance < 0` → enters the branch → calls `(-balance.vQuoteBalance).div(state.openInterest)` where `state.openInterest == 0`.
8. `MathSD21x18.div` reverts with `"DBZ"`.
9. The entire finalization transaction reverts. Alice's subaccount is permanently stuck. [7](#0-6) [8](#0-7)

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

**File:** core/contracts/PerpEngineState.sol (L111-113)
```text
            if (state.openInterest == 0) {
                continue;
            }
```

**File:** core/contracts/ClearinghouseLiq.sol (L279-290)
```text
    function _finalizeSubaccount(
        IEndpoint.LiquidateSubaccount calldata txn,
        ISpotEngine spotEngine,
        IPerpEngine perpEngine
    ) internal returns (bool) {
        if (txn.productId != type(uint32).max) {
            return false;
        }
        // check whether the subaccount can be finalized:
        // - all perps positions have closed
        // - all spread positions have closed
        // - all spot assets have closed
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
