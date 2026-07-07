### Title
Zero-Weight Spot Balances Bypass Liability Liquidation Guard, Creating Socializable Bad Debt — (`core/contracts/ClearinghouseLiq.sol`)

---

### Summary

`_assertCanLiquidateLiability` unconditionally skips any spot product whose `longWeightInitialX18 == 0`. Because those same products are also blocked from being liquidated as spot assets (`ERR_INVALID_PRODUCT`), a liquidatee can hold an arbitrary positive balance in a zero-weight spot product, pass the liability-liquidation gate, have their quote liabilities socialized, and retain the zero-weight tokens — producing bad debt absorbed by all protocol participants.

---

### Finding Description

`liquidateSubaccountImpl` calls `_assertCanLiquidateLiability` whenever `txn.amount < 0` and the product is a spot or spread: [1](#0-0) 

Inside that guard, the loop over all registered spot products contains an early `continue` for any product whose `longWeightInitialX18 == 0`: [2](#0-1) 

The `require(balance.amount <= 0, ...)` check — the only line that enforces "no positive spot assets remain" — is therefore never reached for zero-weight products. A liquidatee holding `balance.amount > 0` in such a product passes the gate silently.

The same skip pattern is repeated in `_finalizeSubaccount`: [3](#0-2) [4](#0-3) 

And `SpotEngine.socializeSubaccount` only iterates over **negative** balances, so the positive zero-weight balance is never redistributed: [5](#0-4) 

The reason zero-weight products cannot be liquidated as spot assets is the explicit guard in `_assertLiquidationAmount`: [6](#0-5) 

This creates a structural deadlock that the `continue` was presumably added to avoid, but the consequence is that the positive asset is permanently stranded in the liquidatee's account while the corresponding liability is socialized.

---

### Impact Explanation

**Exact asset delta / broken invariant:**

| Before liability liquidation | After |
|---|---|
| Liquidatee: `+X` zero-weight tokens, `-Y` USDC quote | Liquidatee: `+X` zero-weight tokens, `0` USDC (covered by insurance/socialization) |
| Protocol insurance / other LPs: `I` | Protocol insurance / other LPs: `I - Y` (partially or fully) |

The invariant "liability liquidation must only occur when all positive spot assets have been exhausted" is broken. The liquidatee retains real on-chain tokens (the zero-weight spot product still has a non-zero `priceX18` stored in `RiskHelper.RiskStore`) while the protocol absorbs the quote liability. [7](#0-6) [8](#0-7) 

---

### Likelihood Explanation

**Preconditions required:**

1. At least one registered spot product with `longWeightInitial == 0` and a non-zero `priceX18` (products in deprecation or with zero collateral factor satisfy this).
2. Liquidatee holds a positive balance in that product (achievable via normal deposit/trade flows).
3. Liquidatee's quote balance is sufficiently negative to be under maintenance health — zero-weight long positions contribute `0` to both initial and maintenance health (`weight = longWeightMaintenanceX18`, which is `>= longWeightInitialX18 = 0`), so the account can be under maintenance health while holding these tokens.
4. No positive balances in normal-weight spot products and no non-spread perp positions (standard pre-condition for liability liquidation).

A liquidatee approaching insolvency has a direct incentive to convert normal-weight spot assets into zero-weight spot tokens before liquidation to shield those assets from transfer to the liquidator.

---

### Recommendation

Replace the `continue` with an explicit assertion that zero-weight spot balances are also non-positive before allowing liability liquidation:

```solidity
// In _assertCanLiquidateLiability, lines 235-244
for (uint32 i = 1; i < spotIds.length; ++i) {
    uint32 spotId = spotIds[i];
    ISpotEngine.Balance memory balance = spotEngine.getBalance(spotId, txn.liquidatee);
    // Enforce no positive spot balance regardless of weight.
    // Zero-weight products cannot be liquidated as assets, so if a positive
    // balance exists here, liability liquidation must be blocked entirely.
    require(balance.amount <= 0, ERR_NOT_LIQUIDATABLE_LIABILITIES);
}
```

Alternatively, introduce a dedicated liquidation path for zero-weight spot products (e.g., forced transfer to the insurance fund at oracle price) so the deadlock is resolved without skipping the invariant check.

---

### Proof of Concept

```
State setup (Hardhat fork, unmodified contracts):
  1. Register spot product P with longWeightInitial=0, longWeightMaintenance=0, priceX18=1e18.
  2. Liquidatee deposits 100 units of P (balance[P] = +100).
  3. Liquidatee borrows 200 USDC (quote balance = -200).
     → maintenance health = 0*100*1 + (-200) = -200 < 0  ✓ under maintenance
  4. Liquidatee has no other spot or perp positions.

Attack call:
  liquidateSubaccount({
      sender: attacker,
      liquidatee: victim,
      productId: QUOTE_PRODUCT_ID+1,   // any normal spot/spread product with liability
      isEncodedSpread: false,
      amount: -200,                     // buy back the USDC liability
      nonce: ...
  })

Trace:
  liquidateSubaccountImpl
    → isUnderMaintenance(victim) = true  ✓
    → _finalizeSubaccount returns false (productId != uint32.max)
    → txn.amount < 0 && spot engine → _assertCanLiquidateLiability called
        loop i=1: spotId=P, longWeightInitialX18==0 → continue  ← BUG
        (no revert)
    → _assertLiquidationAmount, _handleLiquidationPayment execute
    → USDC liability socialized / covered by insurance

Post-state assertion (should fail but passes):
  assert(spotEngine.getBalance(P, victim).amount == 0)  // FAILS: still +100
  assert(insurance >= insurance_before)                  // FAILS: insurance reduced
```

The fuzz assertion from the question — "assert that `_assertCanLiquidateLiability` reverts when any positive balance exists regardless of weight" — will reproduce this failure on the first iteration with `longWeightInitialX18 == 0` and `balance > 0`.

### Citations

**File:** core/contracts/ClearinghouseLiq.sol (L159-163)
```text
        } else if (engine == address(spotEngine)) {
            require(
                spotEngine.getRisk(spotId).longWeightInitialX18 != 0,
                ERR_INVALID_PRODUCT
            );
```

**File:** core/contracts/ClearinghouseLiq.sol (L235-244)
```text
        for (uint32 i = 1; i < spotIds.length; ++i) {
            uint32 spotId = spotIds[i];
            if (spotEngine.getRisk(spotId).longWeightInitialX18 == 0) {
                continue;
            }
            ISpotEngine.Balance memory balance = spotEngine.getBalance(
                spotId,
                txn.liquidatee
            );
            require(balance.amount <= 0, ERR_NOT_LIQUIDATABLE_LIABILITIES);
```

**File:** core/contracts/ClearinghouseLiq.sol (L301-311)
```text
        for (uint32 i = 1; i < v.spotIds.length; ++i) {
            uint32 spotId = v.spotIds[i];
            if (spotEngine.getRisk(spotId).longWeightInitialX18 == 0) {
                continue;
            }
            ISpotEngine.Balance memory balance = spotEngine.getBalance(
                spotId,
                txn.liquidatee
            );
            require(balance.amount <= 0, ERR_NOT_FINALIZABLE_SUBACCOUNT);
        }
```

**File:** core/contracts/ClearinghouseLiq.sol (L379-383)
```text
                if (spotEngine.getRisk(spotId).longWeightInitialX18 == 0) {
                    continue;
                }
                require(balance.amount == 0, ERR_NOT_FINALIZABLE_SUBACCOUNT);
            }
```

**File:** core/contracts/ClearinghouseLiq.sol (L629-637)
```text
        if (
            (txn.amount < 0) &&
            (txn.isEncodedSpread ||
                address(productToEngine[txn.productId]) == address(spotEngine))
        ) {
            // when it's spread or spot liquidation, we need to make sure the liquidatee has
            // enough quote to buyback the liquidated amount.
            _assertCanLiquidateLiability(txn, spotEngine, perpEngine);
            _settlePositivePerpPnl(txn, spotEngine, perpEngine);
```

**File:** core/contracts/SpotEngine.sol (L255-276)
```text
            if (balance.amount < 0) {
                int128 totalDeposited = state.totalDepositsNormalized.mul(
                    state.cumulativeDepositsMultiplierX18
                );

                state.cumulativeDepositsMultiplierX18 = (totalDeposited +
                    balance.amount).div(state.totalDepositsNormalized);

                require(state.cumulativeDepositsMultiplierX18 > 0);

                state.totalBorrowsNormalized += balance.amount.div(
                    state.cumulativeBorrowsMultiplierX18
                );

                _setBalanceAndUpdateBitmap(
                    productId,
                    subaccount,
                    BalanceNormalized({amountNormalized: 0})
                );
                _setState(productId, state);
            }
        }
```

**File:** core/contracts/libraries/RiskHelper.sol (L14-24)
```text
    struct RiskStore {
        // these weights are all
        // between 0 and 2
        // these integers are the real
        // weights times 1e9
        int32 longWeightInitial;
        int32 shortWeightInitial;
        int32 longWeightMaintenance;
        int32 shortWeightMaintenance;
        int128 priceX18;
    }
```

**File:** core/contracts/BaseEngine.sol (L54-60)
```text
        RiskHelper.RiskStore memory s = _risk().value[productId];
        r.longWeightInitialX18 = int128(s.longWeightInitial) * 1e9;
        r.shortWeightInitialX18 = int128(s.shortWeightInitial) * 1e9;
        r.longWeightMaintenanceX18 = int128(s.longWeightMaintenance) * 1e9;
        r.shortWeightMaintenanceX18 = int128(s.shortWeightMaintenance) * 1e9;
        r.priceX18 = s.priceX18;
    }
```
