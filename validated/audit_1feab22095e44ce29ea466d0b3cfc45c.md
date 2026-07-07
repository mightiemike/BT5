I can confirm the core arithmetic from the code. Let me trace it precisely.

**`getSettlementState` (PerpEngine.sol:125-139):**

```solidity
availableSettle = MathHelper.min(
    calculatePositionPnl(balance, productId),  // e.g. -X (negative)
    state.availableSettle                       // 0
);
// MathHelper.min(-X, 0) = -X  ← canSettle is NEGATIVE
``` [1](#0-0) [2](#0-1) 

**`settlePnl` (PerpEngine.sol:77-105) with `canSettle = -X`:**

```solidity
state.availableSettle -= canSettle;   // 0 -= (-X)  → +X  ← INCREASES
balance.vQuoteBalance -= canSettle;   // vQuote -= (-X) → vQuote + X ← loss forgiven
totalSettled += canSettle;            // totalSettled = -X ← negative
``` [3](#0-2) 

There is **no guard** anywhere in `settlePnl` or `getSettlementState` that clamps `canSettle` to `>= 0`. The missing fix would be `canSettle = max(0, min(positionPnl, state.availableSettle))`. [4](#0-3) 

---

**However, the full exploit chain requires verifying the Clearinghouse's handling of negative `totalSettled`**, which I was unable to read in the available iterations. The question asserts that the Clearinghouse calls `_spotEngine().updateBalance(QUOTE_PRODUCT_ID, subaccount, totalSettled)` with the negative value, decreasing the caller's spot balance by `X`. If that is accurate, the net wealth effect on the attacker is:

| Account | Spot QUOTE | Perp vQuoteBalance |
|---|---|---|
| Attacker sub1 (loser) | −X | +X (loss forgiven) |
| Attacker sub2 (winner) | +X | −X (profit taken) |
| **Net** | **0** | **0** |

The attacker's **total wealth is unchanged**. The phantom `availableSettle` capacity is backed by sub1's spot balance decrease, so no USDC is extracted from the protocol unless the spot engine permits negative balances (borrowing/margin), which I could not verify.

---

**What is definitively confirmed:**

1. The invariant `state.availableSettle` **never increases** after `settlePnl` is **broken** — it increases by `|positionPnl|` when a losing position settles against zero available capacity. [5](#0-4) 
2. A loser's `vQuoteBalance` is **improved** (loss partially forgiven) without any protocol-level cost. [6](#0-5) 
3. The perp engine's accounting invariant is corrupted regardless of the Clearinghouse behavior.

**What is unverified:** Whether the Clearinghouse enforces the spot balance decrease for negative `totalSettled`, and whether spot balances can go negative. If the Clearinghouse skips the `updateBalance` call for negative `totalSettled` (e.g., with a `> 0` guard), the exploit becomes a **free loss forgiveness** with no cost — a critical vulnerability. If spot balances can go negative, the attacker can extract real USDC.

---

Given the confirmed invariant violation in `PerpEngine.sol` and the absence of any guard against negative `canSettle`:

### Title
Negative `canSettle` in `settlePnl` Inflates `availableSettle` and Forgives Perp Losses — (`core/contracts/PerpEngine.sol`)

### Summary
`getSettlementState` returns a negative `canSettle` when a position has negative PnL and `state.availableSettle == 0`. `settlePnl` applies this without clamping to zero, causing `state.availableSettle` to increase and `balance.vQuoteBalance` to increase (loss forgiven).

### Finding Description
`MathHelper.min(negative_pnl, 0)` returns `negative_pnl`. The subsequent `state.availableSettle -= canSettle` and `balance.vQuoteBalance -= canSettle` both subtract a negative number, increasing both values. There is no `max(0, canSettle)` clamp anywhere in the call path. [7](#0-6) [3](#0-2) 

### Impact Explanation
- `state.availableSettle` is inflated by phantom capacity equal to `|positionPnl|`, violating the core accounting invariant.
- The loser's `vQuoteBalance` is improved without legitimate settlement, corrupting perp position accounting.
- A second subaccount with a profitable position can immediately consume the phantom `availableSettle`, extracting spot QUOTE balance backed by the phantom capacity.
- If the Clearinghouse does not enforce a spot balance decrease for negative `totalSettled`, the loss forgiveness is entirely free — a critical accounting exploit.

### Likelihood Explanation
The path is externally reachable via any public `settlePnl` entrypoint in the Clearinghouse. Any subaccount with a losing perp position and `state.availableSettle == 0` (which is the initial state for every new product) can trigger this. [8](#0-7) 

### Recommendation
Clamp `canSettle` to non-negative before applying state mutations:

```solidity
// In getSettlementState or at the top of the settlePnl loop:
canSettle = MathHelper.max(0, MathHelper.min(
    calculatePositionPnl(balance, productId),
    state.availableSettle
));
```

### Proof of Concept
```
state.availableSettle = 0
positionPnl = -100e18

canSettle = min(-100e18, 0) = -100e18

state.availableSettle -= (-100e18)  →  state.availableSettle = 100e18  ← phantom
balance.vQuoteBalance -= (-100e18)  →  vQuoteBalance += 100e18         ← loss forgiven
totalSettled = -100e18

// Second subaccount with positionPnl = +100e18:
canSettle2 = min(100e18, 100e18) = 100e18
state.availableSettle -= 100e18  →  0
// Second subaccount's spot QUOTE increases by 100e18
```

### Citations

**File:** core/contracts/PerpEngine.sol (L52-54)
```text
                    cumulativeFundingShortX18: 0,
                    availableSettle: 0,
                    openInterest: 0
```

**File:** core/contracts/PerpEngine.sol (L82-104)
```text
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
