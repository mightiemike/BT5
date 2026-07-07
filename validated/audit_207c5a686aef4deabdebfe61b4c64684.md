### Title
`-INF` Sentinel Not Propagated Through Perp Engine Health Accumulation — (`core/contracts/BaseEngine.sol`, `core/contracts/Clearinghouse.sol`)

---

### Summary

`BaseEngine.getHealthContribution` accumulates per-product health values with plain `+=` arithmetic and never checks whether any individual product returned `-INF`. Separately, `Clearinghouse.getHealth` checks for `-INF` only from the spot engine, not from the perp engine. Either flaw alone is sufficient to let a subaccount holding a position in a delisted/invalid perp product (`weight == 2*ONE`) appear healthy.

---

### Finding Description

**`INF` definition:**

`INF = type(int128).max / 128` ≈ `1.33 × 10^36` [1](#0-0) 

This is deliberately chosen to be far from `type(int128).min` so that arithmetic on it does not overflow in Solidity 0.8+.

**Sentinel emission — correct:**

`_calculateProductHealth` correctly returns `-INF` when a product's weight equals `2 * ONE`: [2](#0-1) 

**Sentinel swallowed — flaw 1:**

`_processBitmapChunk` accumulates each product's contribution with a bare `+=`. When one product returns `-INF`, the loop continues and subsequent products add their values on top of it: [3](#0-2) 

`getHealthContribution` does the same across bitmap chunks: [4](#0-3) 

Concrete arithmetic: if perp product A (delisted, `weight == 2*ONE`) contributes `-INF` and perp product B contributes `+2*INF`, the perp engine returns `+INF` — a large positive — despite the invalid product.

**Sentinel ignored — flaw 2 (independent):**

`Clearinghouse.getHealth` guards against `-INF` from the spot engine but performs an unchecked `+=` with the perp engine's result: [5](#0-4) 

Even in the simplest case — one delisted perp product, no other perp products — the perp engine correctly returns `-INF`, but the clearinghouse adds it to the spot health. With a spot balance of `2*INF`, the total becomes `2*INF + (−INF) = INF > 0`: the subaccount appears healthy.

---

### Impact Explanation

A subaccount holding a position in a delisted or otherwise invalid perp product (`weight == 2*ONE`) bypasses the `-INF` sentinel entirely. The clearinghouse reports a positive health value, allowing the subaccount to:

- Open new borrows or positions against an invalid product's notional value
- Avoid liquidation that should be triggered immediately upon delisting
- Drain protocol insurance or counterparty funds before the inconsistency is detected

This directly breaks solvency accounting and creates or destroys value incorrectly — matching the Critical scope.

---

### Likelihood Explanation

Products are delisted via `delistProduct` (a sequencer-gated path), which sets `weight = 2*ONE`. Any user who held a position in the product before delisting, and also holds a sufficiently large position in another perp product, is immediately affected. No special attacker privileges are required beyond having pre-existing positions; the sequencer's own delisting action triggers the condition.

---

### Recommendation

**Fix 1 — propagate `-INF` inside `_processBitmapChunk` and `getHealthContribution`:**

```solidity
// in _processBitmapChunk
int128 contrib = _calculateProductHealth(productId, subaccount, healthType);
if (contrib == -INF) return -INF;
health += contrib;

// in getHealthContribution
int128 chunkHealth = _processBitmapChunk(...);
if (chunkHealth == -INF) return -INF;
health += chunkHealth;
```

**Fix 2 — check perp engine result in `Clearinghouse.getHealth`:**

```solidity
int128 perpHealth = perpEngine.getHealthContribution(subaccount, healthType);
if (perpHealth == -INF) return -INF;
health += perpHealth;
```

Both fixes are required; either flaw alone is sufficient to break the invariant.

---

### Proof of Concept

Configure a Hardhat test with:

1. Perp product A: `shortWeightMaintenance = 2e9` (→ `weight = 2*ONE` for a short position). Open a short position for the test subaccount.
2. Perp product B: normal weights. Open a long position large enough that its health contribution exceeds `INF`.
3. Spot: deposit enough quote that `spotEngine.getHealthContribution` returns `2*INF`.

Call `clearinghouse.getHealth(subaccount, INITIAL)`.

**Expected (correct):** `-INF` (subaccount has an invalid product).

**Actual (buggy):** A large positive value, because:
- `_calculateProductHealth(A)` → `-INF`
- `_calculateProductHealth(B)` → `+2*INF`
- `perpEngine.getHealthContribution` → `+INF`
- `spotEngine.getHealthContribution` → `+2*INF`
- `getHealth` → `3*INF` (no `-INF` check on perp result) [6](#0-5) [5](#0-4)

### Citations

**File:** core/contracts/common/Constants.sol (L54-54)
```text
int128 constant INF = type(int128).max / 128;
```

**File:** core/contracts/BaseEngine.sol (L112-135)
```text
    function getHealthContribution(
        bytes32 subaccount,
        IProductEngine.HealthType healthType
    ) public returns (int128 health) {
        uint32 maxBitmapIndex = _getMaxProductId() / 256;

        for (
            uint32 bitmapIndex = 0;
            bitmapIndex <= maxBitmapIndex;
            bitmapIndex++
        ) {
            uint256 bitmapChunk = _getBitmapChunk(subaccount, bitmapIndex);
            if (bitmapChunk == 0) {
                continue;
            }

            health += _processBitmapChunk(
                bitmapChunk,
                bitmapIndex,
                subaccount,
                healthType
            );
        }
    }
```

**File:** core/contracts/BaseEngine.sol (L144-154)
```text
        while (bitmapChunk != 0) {
            if (bitmapChunk & 1 != 0) {
                health += _calculateProductHealth(
                    productId,
                    subaccount,
                    healthType
                );
            }
            bitmapChunk >>= 1;
            productId++;
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

**File:** core/contracts/Clearinghouse.sol (L78-84)
```text
        health = spotEngine.getHealthContribution(subaccount, healthType);
        // min health means that it is attempting to borrow a spot that exists outside
        // of the risk system -- return min health to error out this action
        if (health == -INF) {
            return health;
        }
        health += perpEngine.getHealthContribution(subaccount, healthType);
```
