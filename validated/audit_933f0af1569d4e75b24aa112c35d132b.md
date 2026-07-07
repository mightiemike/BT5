### Title
Partial Liquidation Can Temporarily Restore Subaccount to Non-Liquidatable State, Delaying Full Liquidation — (`File: core/contracts/ClearinghouseLiq.sol`)

---

### Summary

`liquidateSubaccountImpl` in `ClearinghouseLiq` checks that the liquidatee is under maintenance health at entry, but the post-liquidation guard only prevents bringing the subaccount *above* initial health. A liquidator can choose a partial amount that moves the subaccount from under-maintenance into the "gray zone" (maintenance health ≥ 0, initial health ≤ 0), making it temporarily immune to further liquidation while still carrying a risky, under-initial position.

---

### Finding Description

`liquidateSubaccountImpl` enforces two health-related invariants:

1. **Entry guard** (line 603): `require(isUnderMaintenance(txn.liquidatee), ERR_NOT_LIQUIDATABLE)` — the subaccount must have MAINTENANCE health < 0 to be liquidated.
2. **Exit guard** (line 573): `require(!isAboveInitial(txn.liquidatee), ERR_LIQUIDATED_TOO_MUCH)` — the liquidation must not push INITIAL health above 0. [1](#0-0) [2](#0-1) 

The protocol's health model has three zones:

| Zone | INITIAL health | MAINTENANCE health | Liquidatable? |
|---|---|---|---|
| Healthy | > 0 | > 0 | No |
| Gray zone | ≤ 0 | ≥ 0 | **No** |
| Liquidatable | < 0 | < 0 | Yes |

`isUnderMaintenance` returns `true` only when MAINTENANCE health < 0. `isAboveInitial` returns `true` only when INITIAL health > 0. [3](#0-2) 

Because maintenance weights are more lenient than initial weights, MAINTENANCE health ≥ INITIAL health for any given position. This means the gray zone (MAINTENANCE ≥ 0, INITIAL ≤ 0) is a reachable intermediate state.

The exit guard at line 573 only blocks landing in zone 1 (INITIAL > 0). It does **not** block landing in the gray zone (MAINTENANCE ≥ 0, INITIAL ≤ 0). A liquidator can therefore choose a `txn.amount` that partially reduces the liquidatee's position just enough to push MAINTENANCE health from negative to zero or slightly positive, while INITIAL health remains negative. After this, the entry guard at line 603 will reject all subsequent liquidation attempts with `ERR_NOT_LIQUIDATABLE`, even though the subaccount is still under-initial and one adverse price move away from being under-maintenance again. [4](#0-3) 

---

### Impact Explanation

A subaccount that should be fully liquidated can be "parked" in the gray zone by a strategically sized partial liquidation. If the market continues to move adversely, the subaccount re-enters the liquidatable zone with a larger loss, increasing the probability that the insurance fund must cover a shortfall. The protocol accumulates bad debt risk during the window in which the subaccount is shielded from further liquidation.

---

### Likelihood Explanation

Any unprivileged liquidator can trigger this by selecting a `txn.amount` that brings MAINTENANCE health to exactly ≥ 0. The liquidator is economically incentivized (they receive a liquidation discount via `getLiqPriceX18`). A subaccount owner can coordinate with a friendly liquidator to exploit this deliberately, since self-liquidation is blocked by line 602 (`require(txn.sender != txn.liquidatee, ERR_UNAUTHORIZED)`), but coordination with any other address is unrestricted. [5](#0-4) 

---

### Recommendation

After `_handleLiquidationPayment` completes, add a check that if the liquidatee's INITIAL health is still negative (i.e., the subaccount is still under-initial), the liquidation must have also brought MAINTENANCE health to < 0 **or** fully closed the position. Concretely, replace the single `!isAboveInitial` guard with a dual check:

```solidity
// After _handleLiquidationPayment:
bool aboveInitial = isAboveInitial(txn.liquidatee);
bool underMaintenance = isUnderMaintenance(txn.liquidatee);
// Must not over-liquidate past initial health
require(!aboveInitial, ERR_LIQUIDATED_TOO_MUCH);
// If still under initial, must not have left the subaccount in the gray zone
// (i.e., must still be under maintenance OR position must be fully closed)
require(aboveInitial || underMaintenance || positionFullyClosed, ERR_PARTIAL_LIQ_GRAY_ZONE);
```

Alternatively, enforce that a single liquidation call must bring the subaccount either to fully healthy (INITIAL ≥ 0) or keep it under maintenance, with no intermediate gray-zone resting state permitted.

---

### Proof of Concept

**Setup:**
- Alice opens a perp position such that her MAINTENANCE health = −5 and INITIAL health = −20.
- Bob (liquidator) calls `liquidateSubaccountImpl` with a `txn.amount` calibrated to reduce Alice's position by exactly enough to bring MAINTENANCE health to 0 (and INITIAL health to, say, −8).

**Step 1 — Entry check passes:**
`isUnderMaintenance(alice)` → `true` (MAINTENANCE = −5 < 0). Line 603 passes.

**Step 2 — Partial liquidation executes:**
`_handleLiquidationPayment` reduces Alice's position. After the call, MAINTENANCE health = 0, INITIAL health = −8.

**Step 3 — Exit check passes:**
`isAboveInitial(alice)` → `false` (INITIAL = −8 ≤ 0). Line 573 passes. Liquidation succeeds.

**Step 4 — Subsequent liquidation blocked:**
Carol calls `liquidateSubaccountImpl` for Alice. `isUnderMaintenance(alice)` → `false` (MAINTENANCE = 0 ≥ 0). Line 603 reverts with `ERR_NOT_LIQUIDATABLE`.

**Step 5 — Price moves adversely:**
Alice's MAINTENANCE health drops to −3 again. The protocol has been exposed to additional risk during the window between steps 3 and 5, and the insurance fund may need to cover a larger shortfall than if Alice had been fully liquidated at step 1. [6](#0-5)

### Citations

**File:** core/contracts/ClearinghouseLiq.sol (L33-58)
```text
    function isUnderInitial(bytes32 subaccount) public returns (bool) {
        // Weighted initial health with limit orders < 0
        return
            getHealthFromClearinghouse(
                subaccount,
                IProductEngine.HealthType.INITIAL
            ) < 0;
    }

    function isAboveInitial(bytes32 subaccount) public returns (bool) {
        // Weighted initial health with limit orders < 0
        return
            getHealthFromClearinghouse(
                subaccount,
                IProductEngine.HealthType.INITIAL
            ) > 0;
    }

    function isUnderMaintenance(bytes32 subaccount) internal returns (bool) {
        // Weighted maintenance health < 0
        return
            getHealthFromClearinghouse(
                subaccount,
                IProductEngine.HealthType.MAINTENANCE
            ) < 0;
    }
```

**File:** core/contracts/ClearinghouseLiq.sol (L572-573)
```text
        // it's ok to let initial health become 0
        require(!isAboveInitial(txn.liquidatee), ERR_LIQUIDATED_TOO_MUCH);
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
