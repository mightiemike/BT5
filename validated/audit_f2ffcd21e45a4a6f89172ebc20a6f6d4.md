### Title
Division by Zero in `PerpEngine.socializeSubaccount` When `openInterest` Is Zero Causes DoS of Liquidation Finalization — (File: `core/contracts/PerpEngine.sol`)

---

### Summary

`PerpEngine.socializeSubaccount` unconditionally divides by `state.openInterest` when spreading residual negative PnL across market participants. If `openInterest` is zero at the time of the call — a reachable state during liquidation finalization when the liquidatee is the sole remaining participant — the transaction reverts with `"DBZ"`, permanently blocking finalization of the insolvent subaccount.

---

### Finding Description

In `PerpEngine.socializeSubaccount`, after insurance partially covers a negative `vQuoteBalance`, the remaining loss is spread across all open-interest holders via:

```solidity
int128 fundingPerShare = -balance.vQuoteBalance.div(
    state.openInterest
);
```

`MathSD21x18.div` enforces `require(y != 0, ERR_DIV_BY_ZERO)`. [1](#0-0) 

`state.openInterest` is the sum of absolute position sizes across all participants. During liquidation finalization (`_finalizeSubaccount`), the code first asserts that every perp position of the liquidatee is zero:

```solidity
require(balance.amount == 0, ERR_NOT_FINALIZABLE_SUBACCOUNT);
``` [2](#0-1) 

Because `balance.amount == 0`, the liquidatee contributes nothing to `openInterest`. If no other subaccount holds an open position in that product, `state.openInterest == 0`. The subsequent call to `perpEngine.socializeSubaccount` then hits the division by zero. [3](#0-2) 

There is no guard of the form `if (state.openInterest == 0) { ... }` anywhere in `socializeSubaccount` before the division. [4](#0-3) 

---

### Impact Explanation

`liquidateSubaccountImpl` → `_finalizeSubaccount` → `perpEngine.socializeSubaccount` is the only path to finalize an insolvent subaccount. A revert here means:

- The liquidatee's account can never be finalized.
- Residual bad debt is never written off or socialized.
- The insurance fund accounting (`insurance` state variable) is never updated for this subaccount.
- The isolated subaccount cleanup path (`tryCloseIsolatedSubaccount`) is never reached.

The corrupted state is: `insurance` remains inflated by `lastLiquidationFees` that were never committed, and the insolvent subaccount persists indefinitely. [5](#0-4) 

---

### Likelihood Explanation

The three conditions required are all reachable without privilege:

1. **`balance.amount == 0` with `balance.vQuoteBalance < 0`**: A user opens a perp position, accumulates negative funding/PnL, then closes the position (setting `amount = 0`) while leaving `vQuoteBalance` negative. This is a normal trading outcome.

2. **`state.openInterest == 0`**: In a new or low-liquidity perp market, the liquidatee may be the only participant, or all other participants may have closed their positions. An attacker can deliberately ensure this by being the sole participant.

3. **Insurance insufficient**: If `insurance < -balance.vQuoteBalance`, the residual triggers the socialization path.

An attacker can self-construct this scenario: open a position in a thin market, accumulate negative PnL, close the position, drain collateral below maintenance margin, and wait for the sequencer to attempt finalization — which will revert every time. [6](#0-5) 

---

### Recommendation

Add a zero-denominator guard in `socializeSubaccount` before the division. If `openInterest == 0`, there are no other participants to absorb the loss; the residual should be absorbed by the insurance fund or written off:

```solidity
if (balance.vQuoteBalance < 0) {
    if (state.openInterest == 0) {
        // No participants to socialize to; absorb via insurance or write off
        balance.vQuoteBalance = 0;
    } else {
        int128 fundingPerShare = -balance.vQuoteBalance.div(state.openInterest);
        state.cumulativeFundingLongX18 += fundingPerShare;
        state.cumulativeFundingShortX18 -= fundingPerShare;
        balance.vQuoteBalance = 0;
    }
}
``` [7](#0-6) 

---

### Proof of Concept

1. Deploy in a perp market with product ID `P`.
2. Attacker opens a long position of size `S` in product `P` (only participant; `openInterest = S`).
3. Attacker closes the position entirely (`amount = 0`), leaving `vQuoteBalance = -X` (negative PnL from fees/funding). `openInterest` returns to `0`.
4. Attacker withdraws collateral until maintenance health < 0.
5. Sequencer submits `liquidateSubaccount` with `productId = type(uint32).max` (finalization).
6. `_finalizeSubaccount` passes all position-zero checks (since `amount == 0`).
7. `perpEngine.socializeSubaccount(liquidatee, insurance)` is called.
8. `balance.vQuoteBalance = -X < 0`; insurance partially covers but `vQuoteBalance` remains negative.
9. `fundingPerShare = X.div(0)` → `MathSD21x18.div` reverts with `"DBZ"`.
10. The entire liquidation transaction reverts. The subaccount can never be finalized. [8](#0-7) [1](#0-0)

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

**File:** core/contracts/ClearinghouseLiq.sol (L598-647)
```text
    function liquidateSubaccountImpl(IEndpoint.LiquidateSubaccount calldata txn)
        external
    {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);
        require(txn.sender != txn.liquidatee, ERR_UNAUTHORIZED);
        require(isUnderMaintenance(txn.liquidatee), ERR_NOT_LIQUIDATABLE);
        require(
            txn.liquidatee != X_ACCOUNT && txn.liquidatee != N_ACCOUNT,
            ERR_NOT_LIQUIDATABLE
        );
        require(
            txn.productId != QUOTE_PRODUCT_ID,
            ERR_INVALID_LIQUIDATION_PARAMS
        );

        ISpotEngine spotEngine = ISpotEngine(
            address(engineByType[IProductEngine.EngineType.SPOT])
        );
        IPerpEngine perpEngine = IPerpEngine(
            address(engineByType[IProductEngine.EngineType.PERP])
        );

        if (_finalizeSubaccount(txn, spotEngine, perpEngine)) {
            if (RiskHelper.isIsolatedSubaccount(txn.liquidatee)) {
                IOffchainExchange(
                    IEndpoint(getEndpoint()).getOffchainExchange()
                ).tryCloseIsolatedSubaccount(txn.liquidatee);
            }
            return;
        }

        if (
            (txn.amount < 0) &&
            (txn.isEncodedSpread ||
                address(productToEngine[txn.productId]) == address(spotEngine))
        ) {
            // when it's spread or spot liquidation, we need to make sure the liquidatee has
            // enough quote to buyback the liquidated amount.
            _assertCanLiquidateLiability(txn, spotEngine, perpEngine);
            _settlePositivePerpPnl(txn, spotEngine, perpEngine);
        }

        _assertLiquidationAmount(txn, spotEngine, perpEngine);

        // beyond this point, we can be sure that we can liquidate the entire
        // liquidation amount knowing that the insurance fund will remain solvent
        // subsequently we can just blast the remainder of the liquidation and
        // cover the quote balance from the insurance fund at the end
        _handleLiquidationPayment(txn, spotEngine, perpEngine);
    }
```

**File:** core/contracts/PerpEngineState.sol (L103-113)
```text
    function updateStates(uint128 dt, int128[] calldata avgPriceDiffs)
        external
        onlyEndpoint
    {
        int128 dtX18 = int128(dt).fromInt();
        for (uint32 i = 0; i < avgPriceDiffs.length; i++) {
            uint32 productId = productIds[i];
            State memory state = states[productId];
            if (state.openInterest == 0) {
                continue;
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
