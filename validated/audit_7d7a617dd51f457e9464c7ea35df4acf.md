Let me read the relevant sections more carefully to confirm the exact logic flow.

### Title
Global `lastLiquidationFees` Manipulation Bypasses Strict Spot-Liability Zero-Check in `_finalizeSubaccount`, Causing Premature Socialization — (`core/contracts/ClearinghouseLiq.sol`)

---

### Summary

`lastLiquidationFees` is a global storage variable updated by every liquidation of any subaccount. `_finalizeSubaccount` subtracts it from `insurance` to compute `v.canLiquidateMore`. When that flag is false, the strict `balance.amount == 0` guard on spot liabilities is skipped entirely. An attacker can perform a large legitimate liquidation to inflate `lastLiquidationFees`, then immediately submit a finalization for a target subaccount that still carries spot liabilities. The insurance fund has sufficient funds to cover those liabilities, but the reduced `v.insurance` causes `socializeSubaccount` to be called instead, incorrectly distributing losses to other depositors.

---

### Finding Description

**Two separate spot-balance checks exist in `_finalizeSubaccount`:**

**Check 1 — early gate (lines 301–311):** requires `balance.amount <= 0`. Negative balances (liabilities) pass. [1](#0-0) 

**Check 2 — strict gate (lines 372–384):** requires `balance.amount == 0`, but is wrapped in `if (v.canLiquidateMore)` and is entirely skipped when that flag is false. [2](#0-1) 

**`v.canLiquidateMore` is computed as:**

```solidity
v.insurance = insurance;
v.insurance -= lastLiquidationFees;          // line 369
v.canLiquidateMore = (quoteBalance.amount + v.insurance) > 0;  // line 370
``` [3](#0-2) 

**`lastLiquidationFees` is a global variable** set by the last call to `_handleLiquidationPayment` for *any* subaccount:

```solidity
lastLiquidationFees = v.liquidationFees;   // line 586
``` [4](#0-3) 

It is declared as a single shared slot in `ClearinghouseStorage`: [5](#0-4) 

Because it is global and not scoped to the subaccount being finalized, any liquidation can overwrite it.

**Insurance cover uses the reduced `v.insurance`, not the full `insurance`:**

```solidity
int128 insuranceCover = MathHelper.min(v.insurance, -quoteBalance.amount);
``` [6](#0-5) 

If `v.insurance ≤ 0`, `insuranceCover = 0` and `spotEngine.socializeSubaccount` is called — even though the actual `insurance` storage variable still holds the full amount. The `lastLiquidationFees` is added back only *after* socialization:

```solidity
v.insurance += lastLiquidationFees;
insurance = v.insurance;
``` [7](#0-6) 

This means the insurance fund retains its balance while losses are socialized to depositors.

---

### Impact Explanation

A subaccount with outstanding spot liabilities is finalized and socialized despite the insurance fund having sufficient capital to cover those liabilities. Other depositors absorb losses that the protocol's own insurance fund should have absorbed. The insurance fund balance is not reduced — it is simply never applied to the liabilities before socialization occurs.

---

### Likelihood Explanation

The attack requires:
1. A large position under maintenance health (normal market condition, no special privilege needed).
2. `insurance` slightly above `lastLiquidationFees` after the large liquidation (achievable by sizing the liquidation appropriately).
3. A target subaccount with all perp positions closed but remaining spot liabilities (a normal intermediate liquidation state).
4. The attacker's two transactions (large liquidation + finalization) processed consecutively by the sequencer — standard FIFO ordering, no sequencer compromise required.

All four conditions are reachable through normal production paths (`liquidateSubaccountImpl` is `external` and gated only by `EndpointGated`). [8](#0-7) 

---

### Recommendation

Scope `lastLiquidationFees` to the specific subaccount being finalized rather than using a single global slot. One approach: store it as a `mapping(bytes32 => int128)` keyed by subaccount, and only read the entry for `txn.liquidatee` inside `_finalizeSubaccount`. Alternatively, pass the fees as a local parameter through the call chain so no persistent global state is needed for this purpose.

---

### Proof of Concept

```
State setup:
  insurance = 1001e18
  lastLiquidationFees = 0

Step 1 — attacker liquidates a large position (any under-maintenance subaccount):
  _handleLiquidationPayment sets:
    insurance += 1000e18  → insurance = 2001e18  (fees added)
    lastLiquidationFees = 1000e18

  (Assume insurance was already 1001e18 before fees, so after: insurance = 2001e18.
   Simplify: set insurance = 1001e18, lastLiquidationFees = 1000e18 for the PoC.)

Step 2 — attacker submits finalization (productId = type(uint32).max) for target subaccount:
  Target state: all perps closed, spot[BTC].amount = -500e18 (liability), quoteBalance = -500e18

  _finalizeSubaccount:
    v.insurance = 1001e18 - 1000e18 = 1e18
    v.canLiquidateMore = (-500e18 + 1e18) > 0  → false
    → strict zero-check loop (lines 373-384) SKIPPED
    → perpEngine.socializeSubaccount called with v.insurance = 1e18
    insuranceCover = min(1e18, 500e18) = 1e18
    v.insurance = 0
    → v.insurance <= 0 → spotEngine.socializeSubaccount(liquidatee) called
    → 499e18 of spot liabilities socialized to depositors
    v.insurance += 1000e18; insurance = 1000e18  (fund restored, losses already socialized)

Assert: socializeSubaccount was called with non-zero spot liabilities
        while insurance (1001e18) > liabilities (500e18).
```

### Citations

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

**File:** core/contracts/ClearinghouseLiq.sol (L368-370)
```text
        v.insurance = insurance;
        v.insurance -= lastLiquidationFees;
        v.canLiquidateMore = (quoteBalance.amount + v.insurance) > 0;
```

**File:** core/contracts/ClearinghouseLiq.sol (L372-384)
```text
        if (v.canLiquidateMore) {
            for (uint32 i = 1; i < v.spotIds.length; ++i) {
                uint32 spotId = v.spotIds[i];
                ISpotEngine.Balance memory balance = spotEngine.getBalance(
                    spotId,
                    txn.liquidatee
                );
                if (spotEngine.getRisk(spotId).longWeightInitialX18 == 0) {
                    continue;
                }
                require(balance.amount == 0, ERR_NOT_FINALIZABLE_SUBACCOUNT);
            }
        }
```

**File:** core/contracts/ClearinghouseLiq.sol (L395-406)
```text
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
```

**File:** core/contracts/ClearinghouseLiq.sol (L410-411)
```text
        v.insurance += lastLiquidationFees;
        insurance = v.insurance;
```

**File:** core/contracts/ClearinghouseLiq.sol (L579-586)
```text
        insurance += v.liquidationFees;

        // if insurance is not enough for making a subaccount healthy, we should
        // use all insurance to buy its liabilities, then socialize the subaccount
        // however, after the first step, insurance funds will be refilled a little bit
        // which blocks the second step, so we keep the fees of the last liquidation and
        // do not use this part in socialization to unblock it.
        lastLiquidationFees = v.liquidationFees;
```

**File:** core/contracts/ClearinghouseLiq.sol (L598-603)
```text
    function liquidateSubaccountImpl(IEndpoint.LiquidateSubaccount calldata txn)
        external
    {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);
        require(txn.sender != txn.liquidatee, ERR_UNAUTHORIZED);
        require(isUnderMaintenance(txn.liquidatee), ERR_NOT_LIQUIDATABLE);
```

**File:** core/contracts/ClearinghouseStorage.sol (L25-25)
```text
    int128 internal lastLiquidationFees;
```
