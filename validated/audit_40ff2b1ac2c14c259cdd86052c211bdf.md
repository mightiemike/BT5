### Title
Division by Zero in `socializeSubaccount()` Blocks Finalization When `openInterest` Is Zero — (`File: core/contracts/PerpEngine.sol`)

---

### Summary

`PerpEngine.socializeSubaccount()` guards its socialization branch with a check on `balance.vQuoteBalance`, but performs the actual division using `state.openInterest`. When `openInterest` is zero — a reachable state when the liquidatee is the sole participant in a perp product — the division panics, permanently blocking finalization of any unhealthy subaccount in that product.

---

### Finding Description

In `PerpEngine.socializeSubaccount()`, the inner guard checks `balance.vQuoteBalance < 0` before attempting to spread the remaining bad debt across all open-interest holders:

```solidity
// actually socialize if still not enough
if (balance.vQuoteBalance < 0) {
    // socialize across all other participants
    int128 fundingPerShare = -balance.vQuoteBalance.div(
        state.openInterest   // <-- division by openInterest, NOT vQuoteBalance
    );
``` [1](#0-0) 

The guard at line 164 verifies `vQuoteBalance`, but the divisor at line 166–168 is `state.openInterest`. These are entirely independent state variables. `openInterest` is the sum of absolute position sizes across all participants in that product:

```solidity
state.openInterest -= balance.amount.abs();
// ...
if (balance.amount > 0) {
    state.openInterest += balance.amount;
} else {
    state.openInterest -= balance.amount;
}
``` [2](#0-1) 

If the liquidatee is the only participant in a given perp product and their position has been fully closed (`amount == 0`), then `openInterest == 0` for that product. A negative `vQuoteBalance` can persist after position closure because it accumulates realized losses and funding payments independently of `amount`. The two variables are decoupled: `vQuoteBalance < 0` does **not** imply `openInterest > 0`.

`socializeSubaccount` is called from `_finalizeSubaccount` in `ClearinghouseLiq`, which itself requires all perp `amount` values to be zero before proceeding:

```solidity
require(balance.amount == 0, ERR_NOT_FINALIZABLE_SUBACCOUNT);
``` [3](#0-2) 

This mandatory pre-condition is precisely what makes `openInterest == 0` reachable: the liquidatee's position must already be closed, zeroing their contribution to `openInterest`. If no other participant holds an open position in that product, `state.openInterest == 0` at the point of the division.

The call chain is:

```
liquidateSubaccountImpl()  [ClearinghouseLiq.sol:598]
  └─ _finalizeSubaccount() [ClearinghouseLiq.sol:279]
       └─ perpEngine.socializeSubaccount() [PerpEngine.sol:141]
            └─ fundingPerShare = -balance.vQuoteBalance.div(state.openInterest)  ← PANIC
``` [4](#0-3) 

---

### Impact Explanation

When the panic fires, `liquidateSubaccountImpl` reverts. The unhealthy subaccount cannot be finalized. Its bad debt cannot be socialized or recovered from the insurance fund. The subaccount remains permanently stuck in an under-maintenance state: no liquidator can close it out, and the insurance fund absorbs an unrecoverable loss. This matches the external report's scoped impacts: **temporary (potentially permanent) freezing of funds** and **theft of unclaimed yield** (insurance fund bad debt that cannot be recovered).

---

### Likelihood Explanation

Any unprivileged user can trigger this by:

1. Opening a perp position in a product where they are the sole participant (e.g., a newly listed product with no other traders).
2. Closing the position at a realized loss, or allowing negative funding to accumulate, so that `amount == 0` but `vQuoteBalance < 0`.
3. Allowing their quote balance to fall below the magnitude of the negative `vQuoteBalance` (e.g., by withdrawing collateral or suffering further losses elsewhere).
4. The subaccount becomes under-maintenance. Any liquidator who calls `liquidateSubaccountImpl` with `productId == type(uint32).max` to finalize it will hit the panic.

No privileged access, governance capture, or external oracle manipulation is required. The attacker does not even need to act maliciously — this can occur organically for any sole participant in a low-liquidity perp product.

---

### Recommendation

Add an explicit zero-guard on `state.openInterest` before the division, mirroring the pattern used in `SpotEngineState._updateState` which guards on `totalDepositsNormalized` before dividing:

```solidity
if (balance.vQuoteBalance < 0) {
    if (state.openInterest == 0) {
        // No other participants to socialize against;
        // absorb entirely from insurance or write off.
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
``` [5](#0-4) 

---

### Proof of Concept

1. Deploy the protocol. Add a new perp product (e.g., `productId = 2`).
2. Alice opens a long position of size `S` in product 2 (she is the only participant; `openInterest = S`).
3. Price drops. Alice closes her position via `OffchainExchange`. Now `amount = 0`, `vQuoteBalance = -L` (realized loss `L`), `openInterest = 0`.
4. Alice withdraws most of her quote collateral so that her quote balance `Q < L`, making her under-maintenance.
5. Bob (liquidator) calls `liquidateSubaccountImpl` with `txn.productId = type(uint32).max` to finalize Alice.
6. `_finalizeSubaccount` confirms `balance.amount == 0` ✓, settles negative PnL against Alice's quote (exhausted), then calls `perpEngine.socializeSubaccount(alice, insurance)`.
7. Inside `socializeSubaccount`: `balance.vQuoteBalance < 0` → insurance partially covers → `balance.vQuoteBalance` still `< 0` → `fundingPerShare = -balance.vQuoteBalance.div(0)` → **Panic 0x12 (division by zero)**.
8. The entire liquidation reverts. Alice's subaccount is permanently un-finalizable. [6](#0-5)

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

**File:** core/contracts/ClearinghouseLiq.sol (L319-320)
```text
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

**File:** core/contracts/SpotEngineState.sol (L277-280)
```text
            if (state.totalDepositsNormalized == 0) {
                continue;
            }
            _updateState(productId, state, dt);
```
