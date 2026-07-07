### Title
Dust Donation to Liquidatee Subaccount Blocks Finalization in `_finalizeSubaccount` — (File: `core/contracts/ClearinghouseLiq.sol`)

---

### Summary

An unprivileged attacker can deposit a trivial amount of a supported spot token directly into a subaccount undergoing liquidation finalization. Because `_finalizeSubaccount` requires all non-USDC spot balances to be `<= 0` before proceeding, a dust deposit makes the balance positive and causes the finalization to revert. Since `depositCollateral` in `Clearinghouse.sol` imposes no ownership check on the recipient subaccount, any caller can credit any non-isolated subaccount, enabling persistent griefing of the liquidation finalization path.

---

### Finding Description

`_finalizeSubaccount` in `ClearinghouseLiq.sol` is invoked when a liquidator submits a `LiquidateSubaccount` transaction with `productId == type(uint32).max`. Before socializing debt and closing the subaccount, it enforces that every supported spot product (those with `longWeightInitialX18 != 0`) has a balance `<= 0`:

```solidity
// all spot assets (except USDC) must be closed out
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
``` [1](#0-0) 

A second, stricter check (`== 0`) is applied later when `v.canLiquidateMore` is true:

```solidity
require(balance.amount == 0, ERR_NOT_FINALIZABLE_SUBACCOUNT);
``` [2](#0-1) 

The root cause is that `Clearinghouse.depositCollateral` applies no ownership check on `txn.sender` — the only guard is that the recipient is not an isolated subaccount:

```solidity
function depositCollateral(IEndpoint.DepositCollateral calldata txn)
    external
    virtual
    onlyEndpoint
{
    require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);
    ...
    spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
``` [3](#0-2) 

Because any user can submit a `DepositCollateral` slow-mode transaction targeting any non-isolated subaccount, an attacker can credit the liquidatee with 1 unit of any supported spot token. This makes `balance.amount = 1 > 0`, causing the `require(balance.amount <= 0, ...)` check to revert on every finalization attempt.

---

### Impact Explanation

The finalization step (`productId == type(uint32).max`) is the terminal phase of the liquidation lifecycle. It is responsible for socializing residual debt against the insurance fund and fully closing the insolvent subaccount. Blocking it:

- Leaves the insolvent subaccount open indefinitely, preventing debt socialization.
- Keeps the insurance fund exposed to the growing deficit of the liquidatee.
- Forces the sequencer/liquidator into a repeated cycle of liquidating dust positions before each finalization attempt, with the attacker re-donating dust after each cycle.
- The attacker's cost per disruption cycle is a single dust deposit (e.g., 1 wei of a supported token), making sustained griefing economically viable.

---

### Likelihood Explanation

**Medium.** The attack requires:
1. Identifying an insolvent subaccount entering the finalization phase (observable on-chain).
2. Holding a trivial amount of any supported spot token.
3. Submitting a `DepositCollateral` slow-mode transaction to the endpoint targeting the liquidatee.

No privileged access, leaked keys, or governance capture is required. The attacker can repeat the donation after each liquidator cleanup cycle at negligible cost.

---

### Recommendation

Introduce a `maxNegligibleAmount` threshold in `_finalizeSubaccount`. Replace the strict `<= 0` check with a tolerance that treats dust-level positive balances as negligible and does not block finalization:

```solidity
require(
    balance.amount <= maxNegligibleAmount,
    ERR_NOT_FINALIZABLE_SUBACCOUNT
);
```

Alternatively, allow `_finalizeSubaccount` to automatically sweep dust positive spot balances into the insurance fund or the `X_ACCOUNT` before performing the zero-balance assertion, removing the attacker's ability to block the check.

---

### Proof of Concept

1. Subaccount `victim` becomes insolvent; sequencer begins liquidating individual positions.
2. Sequencer submits `LiquidateSubaccount(sender=liquidator, liquidatee=victim, productId=type(uint32).max, ...)` to finalize.
3. Before the transaction is processed, attacker submits a slow-mode `DepositCollateral(sender=victim, productId=ETH_PRODUCT_ID, amount=1)` to the endpoint.
4. Endpoint calls `clearinghouse.depositCollateral(txn)`.
5. `require(!RiskHelper.isIsolatedSubaccount(victim))` passes (victim is a regular subaccount).
6. `spotEngine.updateBalance(ETH_PRODUCT_ID, victim, 1e12)` executes (after decimal normalization), setting `victim`'s ETH balance to `+1e12`.
7. Sequencer's finalization transaction executes `_finalizeSubaccount`.
8. Loop reaches ETH product: `balance.amount = 1e12 > 0`.
9. `require(balance.amount <= 0, ERR_NOT_FINALIZABLE_SUBACCOUNT)` reverts.
10. Attacker repeats step 3 after each sequencer cleanup, sustaining the disruption indefinitely at dust cost. [1](#0-0) [3](#0-2)

### Citations

**File:** core/contracts/ClearinghouseLiq.sol (L300-311)
```text
        // all spot assets (except USDC) must be closed out
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

**File:** core/contracts/Clearinghouse.sol (L193-209)
```text
    function depositCollateral(IEndpoint.DepositCollateral calldata txn)
        external
        virtual
        onlyEndpoint
    {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);
        require(txn.amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        ISpotEngine spotEngine = _spotEngine();
        uint8 decimals = _decimals(txn.productId);

        require(decimals <= MAX_DECIMALS);
        int256 multiplier = int256(10**(MAX_DECIMALS - decimals));
        int128 amountRealized = int128(txn.amount) * int128(multiplier);

        spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
        emit ModifyCollateral(amountRealized, txn.sender, txn.productId);
    }
```
