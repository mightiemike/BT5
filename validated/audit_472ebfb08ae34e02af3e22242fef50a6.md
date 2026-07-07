### Title
Division by Zero in `PerpEngine.socializeSubaccount()` When `openInterest` Is Zero Blocks Liquidation — (File: `core/contracts/PerpEngine.sol`)

### Summary
`PerpEngine.socializeSubaccount()` divides by `state.openInterest` without checking whether it is zero. When a perp market has no open positions (`openInterest == 0`) but a subaccount still carries a negative `vQuoteBalance`, the division reverts, permanently blocking the liquidation/socialization path for that subaccount.

### Finding Description
In `PerpEngine.socializeSubaccount()`, after exhausting insurance coverage, the protocol attempts to distribute remaining losses across all open-interest holders:

```solidity
if (balance.vQuoteBalance < 0) {
    // socialize across all other participants
    int128 fundingPerShare = -balance.vQuoteBalance.div(
        state.openInterest   // ← no zero-check
    );
```

`state.openInterest` is the market-wide total open interest returned by `getStateAndBalance()`. It is zero when no participant holds an open position. A subaccount can legitimately reach `amount == 0` (position closed) while retaining a negative `vQuoteBalance` (accumulated funding losses not fully settled at close). If the market simultaneously has `openInterest == 0` — all other participants have also closed — the `MathSD21x18.div()` call at line 166 reverts with `"DBZ"` because `MathSD21x18.div()` enforces `require(y != 0, ERR_DIV_BY_ZERO)`. [1](#0-0) [2](#0-1) 

### Impact Explanation
When the revert fires, the entire `liquidateSubaccount` transaction reverts. The insolvent subaccount cannot be liquidated or socialized. Its negative `vQuoteBalance` remains permanently unresolved in protocol state, corrupting the `availableSettle` accounting and preventing the protocol from recovering from the insolvency. This is a solvency/accounting corruption impact, not merely a gas DoS. [3](#0-2) 

### Likelihood Explanation
The condition requires two simultaneous facts: (1) a subaccount has `amount == 0` and `vQuoteBalance < 0`, and (2) `state.openInterest == 0`. Both are reachable in a low-liquidity or newly-launched perp market where a single trader opens a position, accumulates negative funding, closes the position (leaving `vQuoteBalance < 0`, `amount == 0`), and no other participant holds an open position. The liquidator is an unprivileged caller who triggers the path. Likelihood is low-to-medium in thin markets. [4](#0-3) 

### Recommendation
Add an explicit zero-guard before the division in `socializeSubaccount()`. If `state.openInterest == 0`, there are no counterparties to absorb the loss; the remaining negative balance should either be written off against the insurance fund entirely or the function should skip socialization for that product:

```solidity
if (balance.vQuoteBalance < 0) {
    if (state.openInterest == 0) {
        // No open interest to socialize against; absorb via insurance or skip
        balance.vQuoteBalance = 0;
    } else {
        int128 fundingPerShare = -balance.vQuoteBalance.div(state.openInterest);
        state.cumulativeFundingLongX18 += fundingPerShare;
        state.cumulativeFundingShortX18 -= fundingPerShare;
        balance.vQuoteBalance = 0;
    }
}
```

### Proof of Concept

1. A perp market (e.g., `productId = 2`) is initialized with `openInterest = 0`.
2. Trader A opens a long position → `openInterest = X`.
3. Funding rate is negative for longs; `cumulativeFundingLongX18` increases, making Trader A's `vQuoteBalance` negative.
4. Trader A closes the position: `_updateBalance` settles funding into `vQuoteBalance`, sets `amount = 0`, reduces `openInterest` back to 0. `vQuoteBalance` remains negative.
5. No other participant holds a position; `state.openInterest == 0`.
6. Trader A's account becomes subject to socialization (e.g., `vQuoteBalance < 0` and insurance is insufficient).
7. Liquidator calls `endpoint.submitTransactions(...)` → `ClearinghouseLiq.liquidateSubaccount` → `perpEngine.socializeSubaccount(subaccount, insurance)`.
8. Inside `socializeSubaccount`, `balance.vQuoteBalance < 0` is true, insurance is exhausted, and `state.openInterest == 0`.
9. `MathSD21x18.div(-balance.vQuoteBalance, 0)` reverts with `"DBZ"`.
10. The entire liquidation transaction reverts; the insolvent subaccount is permanently stuck. [3](#0-2) [5](#0-4)

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

**File:** core/contracts/PerpEngineState.sol (L23-52)
```text
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
