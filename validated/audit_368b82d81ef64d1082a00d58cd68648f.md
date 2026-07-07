### Title
Division by Zero in `socializeSubaccount()` Blocks Liquidation Finalization When Perp Market Has Zero Open Interest — (`File: core/contracts/PerpEngine.sol`)

---

### Summary

`PerpEngine.socializeSubaccount()` performs an unguarded division by `state.openInterest` when spreading residual negative `vQuoteBalance` across market participants. If `state.openInterest` is zero at the time of socialization, `MathSD21x18.div` reverts with `"DBZ"`, permanently blocking the liquidation finalization path for any insolvent subaccount in that perp market.

---

### Finding Description

In `PerpEngine.socializeSubaccount()`, when a subaccount's `vQuoteBalance` remains negative after insurance coverage is applied, the protocol attempts to spread the remaining loss across all open-interest holders:

```solidity
// core/contracts/PerpEngine.sol, line 164-171
if (balance.vQuoteBalance < 0) {
    // socialize across all other participants
    int128 fundingPerShare = -balance.vQuoteBalance.div(
        state.openInterest   // <-- no zero-check
    );
    state.cumulativeFundingLongX18 += fundingPerShare;
    state.cumulativeFundingShortX18 -= fundingPerShare;
    balance.vQuoteBalance = 0;
}
```

`MathSD21x18.div` unconditionally requires its denominator to be non-zero:

```solidity
// core/contracts/libraries/MathSD21x18.sol, line 62-68
function div(int128 x, int128 y) internal pure returns (int128) {
    unchecked {
        require(y != 0, ERR_DIV_BY_ZERO);   // reverts "DBZ"
        ...
    }
}
```

There is no guard in `socializeSubaccount` to skip or handle the case where `state.openInterest == 0`.

The function is called from `ClearinghouseLiq` during liquidation finalization:

```solidity
// core/contracts/ClearinghouseLiq.sol, line 386-389
v.insurance = perpEngine.socializeSubaccount(
    txn.liquidatee,
    v.insurance
);
``` [1](#0-0) [2](#0-1) [3](#0-2) 

---

### Impact Explanation

When the revert fires, the entire `liquidateSubaccountImpl` transaction reverts. The insolvent subaccount cannot be finalized: its bad debt is never socialized, its positions are never zeroed, and the protocol's solvency accounting (`cumulativeFundingLongX18`, `cumulativeFundingShortX18`, `availableSettle`) is never updated. The subaccount remains permanently stuck in an insolvent state that cannot be resolved on-chain, corrupting the perp engine's global state and blocking any future liquidation attempt for the same subaccount in the affected product.

**Impact: 3** — Liquidation finalization is blocked; bad debt is unresolvable; perp market accounting is permanently corrupted for the affected product. [4](#0-3) 

---

### Likelihood Explanation

The trigger condition — `state.openInterest == 0` while a subaccount has `balance.vQuoteBalance < 0` — is reachable in the following realistic scenario:

1. A subaccount accumulates a negative `vQuoteBalance` in a perp product (e.g., from adverse funding payments or a losing trade that was partially closed).
2. The subaccount's perp `amount` is zero (position already closed or fully transferred to a liquidator in a prior liquidation step), but `vQuoteBalance` remains negative.
3. All other participants in that perp market have also closed their positions, leaving `state.openInterest == 0`.
4. The subaccount's overall health is negative (e.g., due to the negative `vQuoteBalance` exceeding quote collateral).
5. Any liquidator submits a `LiquidateSubaccount` transaction targeting this subaccount.
6. The finalization path calls `perpEngine.socializeSubaccount()`, which hits the division by zero and reverts.

This is especially likely in low-activity or newly listed perp markets where the liquidatee may be the last or only participant.

**Likelihood: 3** — Requires a specific but realistic market state; no privileged access needed. [5](#0-4) 

---

### Recommendation

Add a zero-check on `state.openInterest` before the division. If `openInterest` is zero, there are no other participants to socialize the loss to; the loss should either be absorbed by the insurance fund, written off, or the function should return early:

```solidity
if (balance.vQuoteBalance < 0) {
    if (state.openInterest != 0) {
        int128 fundingPerShare = -balance.vQuoteBalance.div(
            state.openInterest
        );
        state.cumulativeFundingLongX18 += fundingPerShare;
        state.cumulativeFundingShortX18 -= fundingPerShare;
    }
    // If openInterest == 0, no participants to socialize to;
    // loss is written off (or handled by insurance at a higher level).
    balance.vQuoteBalance = 0;
}
``` [6](#0-5) 

---

### Proof of Concept

**State setup:**
- Perp product `productId = P` exists with `state.openInterest = 0` (all positions closed).
- Subaccount `S` has `balance.amount = 0` (no open position) but `balance.vQuoteBalance = -1000e18` (negative from prior trading losses or funding).
- Subaccount `S` has insufficient quote collateral to cover the negative `vQuoteBalance`, making it insolvent (maintenance health < 0).

**Attack path:**
1. Any liquidator calls the endpoint with a signed `LiquidateSubaccount` transaction targeting subaccount `S` for product `P`.
2. `Endpoint` → `liquidateSubaccountImpl` in `ClearinghouseLiq`.
3. Health checks confirm `S` is under maintenance.
4. The liquidation step processes (no perp amount to transfer since `amount == 0`).
5. `_finalizeLiquidation` is reached; insurance is insufficient to cover the full `-1000e18`.
6. `perpEngine.socializeSubaccount(S, insurance)` is called.
7. `balance.vQuoteBalance < 0` → true; after partial insurance cover, still negative.
8. `state.openInterest == 0` → `MathSD21x18.div` reverts with `"DBZ"`.
9. Entire transaction reverts. Subaccount `S` remains insolvent and unliquidatable forever. [4](#0-3) [7](#0-6)

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

**File:** core/contracts/ClearinghouseLiq.sol (L383-412)
```text
            }
        }

        v.insurance = perpEngine.socializeSubaccount(
            txn.liquidatee,
            v.insurance
        );

        // we can assure that quoteBalance must be non positive, because if quoteBalance.amount > 0,
        // there must be 1) no negative pnl in perps, and 2) no liabilities in spot after above actions.
        // however, in this case the liquidatee must be healthy and cannot pass the health check at
        // the beginning.
        int128 insuranceCover = MathHelper.min(
            v.insurance,
            -quoteBalance.amount
        );
        if (insuranceCover > 0) {
            v.insurance -= insuranceCover;
            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                txn.liquidatee,
                insuranceCover
            );
        }
        if (v.insurance <= 0) {
            spotEngine.socializeSubaccount(txn.liquidatee);
        }
        v.insurance += lastLiquidationFees;
        insurance = v.insurance;
        return true;
```
