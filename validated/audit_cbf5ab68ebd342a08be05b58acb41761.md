### Title
Swapped Weight Selection in `RiskHelper._getSpreadWeightX18` Corrupts Spread Health Bonus — (`core/contracts/libraries/RiskHelper.sol`)

---

### Summary

`RiskHelper._getSpreadWeightX18` selects the wrong product's long weight when computing the spread health bonus. When the spot leg is long, the function uses the **perp's** long weight; when the spot leg is short, it uses the **spot's** long weight. Both cases are inverted relative to the correct logic. This is a direct argument-order confusion analog to the VaderRouter H-16 bug: a nested calculation uses the wrong pool/product's parameter, corrupting the output for every spread position.

---

### Finding Description

`Clearinghouse.getHealth` computes a spread health adjustment for subaccounts holding matched spot+perp positions (basis trades). It calls `RiskHelper._getSpreadWeightX18` to obtain a spread weight that is supposed to be 5× closer to `ONE` than the underlying product's long weight, granting a health bonus for hedged positions.

The function signature is:

```solidity
function _getSpreadWeightX18(
    IProductEngine.CoreRisk memory perpCoreRisk,
    IProductEngine.CoreRisk memory spotCoreRisk,
    IProductEngine.HealthType healthType
) internal pure returns (int128)
``` [1](#0-0) 

The body selects the weight as follows:

```solidity
if (spotCoreRisk.amount > 0) {
    spreadWeight = ONE - (ONE - perpCoreRisk.longWeight) / 5;
} else {
    spreadWeight = ONE - (ONE - spotCoreRisk.longWeight) / 5;
}
``` [2](#0-1) 

**When `spotCoreRisk.amount > 0`** (long spot, short perp — a "long spread"):
- The dominant long leg is the **spot**; the spread weight should be derived from `spotCoreRisk.longWeight`.
- The code instead uses `perpCoreRisk.longWeight`. ❌

**When `spotCoreRisk.amount <= 0`** (short spot, long perp — a "short spread"):
- The dominant long leg is the **perp**; the spread weight should be derived from `perpCoreRisk.longWeight`.
- The code instead uses `spotCoreRisk.longWeight`. ❌

Both branches are inverted. The correct logic is:

```solidity
if (spotCoreRisk.amount > 0) {
    spreadWeight = ONE - (ONE - spotCoreRisk.longWeight) / 5;   // spot is long
} else {
    spreadWeight = ONE - (ONE - perpCoreRisk.longWeight) / 5;   // perp is long
}
```

`getCoreRisk` always populates `CoreRisk.longWeight` using `_getWeightX18(risk, 1, healthType)` — i.e., the long weight regardless of actual position direction — so the two weights are distinct and product-specific. [3](#0-2) 

The call site in `Clearinghouse.getHealth` passes `perpCoreRisk` first and `spotCoreRisk` second, matching the function signature, so the bug is entirely inside `_getSpreadWeightX18`'s body. [4](#0-3) 

The resulting `spreadWeight` feeds directly into the health delta:

```solidity
health += basisAmount
    .mul(spotCoreRisk.price + perpCoreRisk.price)
    .mul(spreadWeight - existingWeight);
``` [5](#0-4) 

---

### Impact Explanation

Spot products typically carry higher long weights (lower margin requirements) than perp products. Under this common configuration:

| Spread direction | Correct weight source | Actual weight source | Effect |
|---|---|---|---|
| Long spread (long spot, short perp) | `spotCoreRisk.longWeight` (higher) | `perpCoreRisk.longWeight` (lower) | `spreadWeight` is **too low** → health bonus is **understated** → overly conservative |
| Short spread (short spot, long perp) | `perpCoreRisk.longWeight` (lower) | `spotCoreRisk.longWeight` (higher) | `spreadWeight` is **too high** → health bonus is **overstated** → **undercollateralization** |

For the short-spread case, the inflated `spreadWeight` makes `spreadWeight - existingWeight` larger than it should be. Because `basisAmount` is negative for a short spread, the health delta `basisAmount * (prices) * (spreadWeight - existingWeight)` becomes **less negative** (i.e., the health penalty is reduced). A subaccount holding a short spread is therefore credited with more health than it deserves, allowing it to open additional positions beyond what the risk model permits. If the market moves adversely, the subaccount may become insolvent before the protocol can liquidate it, causing losses to the insurance fund.

---

### Likelihood Explanation

Spread positions (basis trades) are a first-class feature of the protocol — the `spreads` bitmask is set at initialization and the spread health adjustment runs on every `getHealth` call, which is invoked on every deposit withdrawal, liquidation check, and order match. Any subaccount that holds a short spread (short spot + long perp) is affected on every health evaluation. No special permissions or unusual conditions are required; a normal trader opening a short spread triggers the bug immediately.

---

### Recommendation

Swap the weight sources in `_getSpreadWeightX18` so that the long leg's weight is always used:

```solidity
if (spotCoreRisk.amount > 0) {
    // long spread: spot is the long leg
    spreadWeight = ONE - (ONE - spotCoreRisk.longWeight) / 5;
} else {
    // short spread: perp is the long leg
    spreadWeight = ONE - (ONE - perpCoreRisk.longWeight) / 5;
}
``` [2](#0-1) 

---

### Proof of Concept

**Setup:** Two products registered as a spread pair. Spot long weight = 0.9e18, perp long weight = 0.8e18.

**Position:** Subaccount holds −100 spot (short) and +100 perp (long) — a short spread.

**Correct computation:**
- Long leg is perp → `perpCoreRisk.longWeight = 0.8e18`
- `spreadWeight = 1 - (1 - 0.8) / 5 = 1 - 0.04 = 0.96`

**Actual computation (buggy):**
- Code uses `spotCoreRisk.longWeight = 0.9e18`
- `spreadWeight = 1 - (1 - 0.9) / 5 = 1 - 0.02 = 0.98`

**Health delta difference:**
- `existingWeight = (0.9 + 0.8) / 2 = 0.85`
- Correct: `basisAmount * prices * (0.96 - 0.85)` = health delta using factor `0.11`
- Buggy: `basisAmount * prices * (0.98 - 0.85)` = health delta using factor `0.13`

The buggy code grants ~18% more spread health bonus than the risk model intends (`0.13 / 0.11 ≈ 1.18`), allowing the subaccount to carry proportionally more leverage. A trader can exploit this by repeatedly opening short spread positions to maximize the inflated health credit, then withdrawing collateral or opening additional directional positions that would be blocked under correct accounting.

### Citations

**File:** core/contracts/libraries/RiskHelper.sol (L57-81)
```text
    function _getSpreadWeightX18(
        IProductEngine.CoreRisk memory perpCoreRisk,
        IProductEngine.CoreRisk memory spotCoreRisk,
        IProductEngine.HealthType healthType
    ) internal pure returns (int128) {
        if (healthType == IProductEngine.HealthType.PNL) {
            return ONE;
        }
        int128 spreadWeight;
        if (spotCoreRisk.amount > 0) {
            spreadWeight = ONE - (ONE - perpCoreRisk.longWeight) / 5;
        } else {
            spreadWeight = ONE - (ONE - spotCoreRisk.longWeight) / 5;
        }
        int128 maxSpreadWeight;
        if (healthType == IProductEngine.HealthType.INITIAL) {
            maxSpreadWeight = ONE - ONE / 100; // 0.99
        } else {
            maxSpreadWeight = ONE - (ONE * 6) / 1000; // 0.994
        }
        if (spreadWeight > maxSpreadWeight) {
            spreadWeight = maxSpreadWeight;
        }
        return spreadWeight;
    }
```

**File:** core/contracts/BaseEngine.sol (L186-192)
```text
        return
            IProductEngine.CoreRisk(
                amount,
                risk.priceX18,
                RiskHelper._getWeightX18(risk, 1, healthType)
            );
    }
```

**File:** core/contracts/Clearinghouse.sol (L127-131)
```text
            int128 spreadWeight = RiskHelper._getSpreadWeightX18(
                perpCoreRisk,
                spotCoreRisk,
                healthType
            );
```

**File:** core/contracts/Clearinghouse.sol (L133-135)
```text
            health += basisAmount
                .mul(spotCoreRisk.price + perpCoreRisk.price)
                .mul(spreadWeight - existingWeight);
```
