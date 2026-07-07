### Title
Division by Zero in `PerpEngine.socializeSubaccount` When `openInterest` Is Zero Blocks Liquidation — (File: `core/contracts/PerpEngine.sol`)

---

### Summary

`PerpEngine.socializeSubaccount` unconditionally divides by `state.openInterest` without a zero-guard. When all perp positions in a product are closed but a subaccount still carries a negative `vQuoteBalance`, `openInterest` is zero and the division reverts, permanently blocking liquidation of that subaccount.

---

### Finding Description

The external report's vulnerability class is **unsafe use of returned state data without a null/zero check before arithmetic**. In the TypeScript snap, `persistedData` returned from `snap_manageState` could be `null`, and the code used `persistedData.popuptoggle` directly as a divisor/operand without a null guard. The Nado analog is structurally identical: a value read from on-chain state (`state.openInterest`) is used directly as a divisor without checking whether it is zero.

In `PerpEngine.socializeSubaccount`, after insurance is applied, the residual negative `vQuoteBalance` is spread across all open-interest holders:

```solidity
// actually socialize if still not enough
if (balance.vQuoteBalance < 0) {
    // socialize across all other participants
    int128 fundingPerShare = -balance.vQuoteBalance.div(
        state.openInterest          // ← no zero-check
    );
```

`state.openInterest` is the sum of absolute position sizes for the product, read from `states[productId]` via `getStateAndBalance`. It is zero whenever no subaccount holds an open position in that product.

A subaccount can simultaneously satisfy `balance.amount == 0` (no open position, contributing nothing to `openInterest`) and `balance.vQuoteBalance < 0` (negative residual from funding accrual or a partially-settled close). If the insurance fund is also insufficient to cover the full loss, the inner branch is entered and `MathSD21x18.div` reverts on the zero denominator, rolling back the entire liquidation call. [1](#0-0) [2](#0-1) 

---

### Impact Explanation

The revert propagates through `ClearinghouseLiq` and causes the entire liquidation transaction to fail. The bad debt cannot be socialized, the insurance fund balance is not updated, and the under-collateralized subaccount remains permanently unliquidatable. The protocol accumulates irrecoverable bad debt with no on-chain path to resolution.

The corrupted state delta is: `state.cumulativeFundingLongX18` and `state.cumulativeFundingShortX18` are never updated, `balance.vQuoteBalance` is never zeroed, and the insurance deduction is never committed — all because the transaction reverts before any storage write occurs. [3](#0-2) 

---

### Likelihood Explanation

The preconditions are:

1. **`balance.amount == 0` with `balance.vQuoteBalance < 0`**: Realistic after a position is closed while funding debt remains, or after a partial settlement leaves residual vQuote debt. Health contribution from `vQuoteBalance` is added unconditionally in `_calculateProductHealth` (`health += quoteAmount`), so negative `vQuoteBalance` alone can push health below zero and trigger liquidation.

2. **`state.openInterest == 0`**: All other subaccounts have closed their positions in that product. This is the normal end-state of a low-activity or winding-down market. A malicious actor can also deliberately close all their own positions in a product immediately before a liquidation attempt to manufacture this condition.

3. **Insurance insufficient**: The insurance fund is finite and can be depleted by prior socializations.

All three conditions can co-occur without any privileged access. [4](#0-3) [5](#0-4) 

---

### Recommendation

Add an explicit zero-guard before the division. If `openInterest` is zero there are no counterparties to absorb the loss; the residual should be written off directly (absorbed by the protocol or marked as irrecoverable) rather than attempting to spread it:

```solidity
if (balance.vQuoteBalance < 0) {
    if (state.openInterest == 0) {
        // No open positions to socialize against; write off residual directly
        balance.vQuoteBalance = 0;
    } else {
        int128 fundingPerShare = -balance.vQuoteBalance.div(state.openInterest);
        state.cumulativeFundingLongX18 += fundingPerShare;
        state.cumulativeFundingShortX18 -= fundingPerShare;
        balance.vQuoteBalance = 0;
    }
}
``` [6](#0-5) 

---

### Proof of Concept

1. Product `P` is registered. All subaccounts close their positions; `states[P].openInterest == 0`.
2. Subaccount `A` has `balance.amount == 0` and `balance.vQuoteBalance == -100e18` (residual funding debt). Its total health is negative; it is eligible for liquidation.
3. Insurance fund holds `50e18` (insufficient to cover the full loss).
4. Liquidator calls `Endpoint.liquidateSubaccount` targeting `A`.
5. `ClearinghouseLiq` calls `PerpEngine.socializeSubaccount(A, 50e18)`.
6. Inside the loop for product `P`: `insuranceCover = min(50e18, 100e18) = 50e18`; `balance.vQuoteBalance` becomes `-50e18`.
7. Inner `if (balance.vQuoteBalance < 0)` is entered.
8. `fundingPerShare = 50e18.div(0)` — division by zero, transaction reverts.
9. Subaccount `A` remains unliquidatable; bad debt is permanently unresolvable. [7](#0-6) [8](#0-7)

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

**File:** core/contracts/PerpEngineState.sol (L16-51)
```text
    mapping(uint32 => State) public states;
    mapping(uint32 => mapping(bytes32 => Balance)) public balances;

    // we use this to track if we have migrated the state to the new format
    // currently we have migrationFlag = 1
    uint128 public migrationFlag;

    function _updateBalance(
        State memory state,
        Balance memory balance,
        int128 balanceDelta,
        int128 vQuoteDelta
    ) internal pure {
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

**File:** core/contracts/PerpEngineState.sol (L54-63)
```text
    function getStateAndBalance(uint32 productId, bytes32 subaccount)
        public
        view
        returns (State memory, Balance memory)
    {
        State memory state = states[productId];
        Balance memory balance = balances[productId][subaccount];
        _updateBalance(state, balance, 0, 0);
        return (state, balance);
    }
```

**File:** core/contracts/BaseEngine.sol (L157-177)
```text
    function _calculateProductHealth(
        uint32 productId,
        bytes32 subaccount,
        IProductEngine.HealthType healthType
    ) internal returns (int128 health) {
        RiskHelper.Risk memory risk = _risk(productId);
        (int128 amount, int128 quoteAmount) = _getBalance(
            productId,
            subaccount
        );
        int128 weight = RiskHelper._getWeightX18(risk, amount, healthType);
        health += quoteAmount;

        if (amount != 0) {
            if (weight == 2 * ONE) {
                return -INF;
            }
            health += amount.mul(weight).mul(risk.priceX18);
            emit PriceQuery(productId);
        }
    }
```
