### Title
Self-Liquidation via Multiple Subaccounts Bypasses `sender != liquidatee` Identity Check — (File: `core/contracts/ClearinghouseLiq.sol`)

---

### Summary

`ClearinghouseLiq.liquidateSubaccountImpl()` contains a check `require(txn.sender != txn.liquidatee, ERR_UNAUTHORIZED)` intended to prevent a subaccount from liquidating itself. However, because Nado subaccounts are `bytes32` values where only the first 20 bytes encode the owner's Ethereum address, a single wallet can control multiple distinct subaccounts. The identity check compares full `bytes32` values, not underlying owner addresses, so a user can liquidate their own underwater subaccount from a different subaccount they control — directly analogous to the NFT auction owner bidding with a different wallet.

---

### Finding Description

Subaccounts in Nado are `bytes32` values whose upper 20 bytes encode the owner's Ethereum address and whose lower bytes encode a subaccount identifier. This is confirmed by the nonce tracking in `EndpointTx.sol`:

```solidity
nonce == nonces[address(uint160(bytes20(sender)))]++
``` [1](#0-0) 

A single address therefore controls an arbitrary number of distinct `bytes32` subaccounts (e.g., `alice:0`, `alice:1`, etc.). The liquidation guard in `ClearinghouseLiq.liquidateSubaccountImpl()` is:

```solidity
require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);
require(txn.sender != txn.liquidatee, ERR_UNAUTHORIZED);
require(isUnderMaintenance(txn.liquidatee), ERR_NOT_LIQUIDATABLE);
``` [2](#0-1) 

The check `txn.sender != txn.liquidatee` only compares the full `bytes32` identifiers. It does **not** compare `address(uint160(bytes20(txn.sender))) != address(uint160(bytes20(txn.liquidatee)))`. A user whose `alice:0` is healthy and whose `alice:1` is underwater can submit a `LiquidateSubaccount` transaction with `sender = alice:0` and `liquidatee = alice:1`. The check passes because the two `bytes32` values differ, even though both are owned by the same Ethereum address.

The liquidation payment mechanics in `_handleLiquidationPayment` transfer the liquidated position to `txn.sender` at the liquidation price (below oracle), and charge liquidation fees to the insurance fund:

```solidity
v.liquidationPayment = v.liquidationPriceX18.mul(txn.amount);
v.liquidationFees = (v.oraclePriceX18 - v.liquidationPriceX18)
    .mul(LIQUIDATION_FEE_FRACTION)
    .mul(txn.amount);
perpEngine.updateBalance(txn.productId, txn.liquidatee, -txn.amount, v.liquidationPayment);
perpEngine.updateBalance(txn.productId, txn.sender, txn.amount, -v.liquidationPayment);
spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -v.liquidationFees);
``` [3](#0-2) 

The attacker's `alice:0` acquires the position at `liquidationPrice`, which is below oracle. Closing it at oracle price yields `(oraclePrice − liquidationPrice) × amount − liquidationFees` in profit. When `alice:1` is deeply insolvent, the insurance fund absorbs the residual shortfall, meaning the attacker can extract value from the insurance fund.

---

### Impact Explanation

**Concrete corrupted state:** The insurance fund balance (`insurance` in `ClearinghouseLiq`) is drained by the shortfall of the self-liquidated subaccount, while the attacker's healthy subaccount captures the liquidation discount. External liquidators are deprived of the discount they would otherwise earn for performing a socially necessary function.

**Worst-case scenario:** An attacker opens a large leveraged position in `alice:1`, allows it to go deeply underwater, then self-liquidates via `alice:0`. `alice:0` receives the position at a steep discount; the insurance fund covers the insolvency gap in `alice:1`. This is a direct, repeatable drain on the insurance fund.

---

### Likelihood Explanation

**Medium-High.** Creating a second subaccount requires only a deposit transaction. Any user with an underwater position and a funded second subaccount can execute this. The `LiquidateSubaccount` transaction is user-initiated (it carries a `sender` and `nonce` field), so the attacker signs and submits it to the sequencer without requiring any privileged cooperation. The sequencer has no on-chain obligation to reject same-owner liquidations, and the on-chain contract is the only enforcement layer — which is insufficient. [4](#0-3) 

---

### Recommendation

Replace the identity check with an owner-address comparison:

```solidity
require(
    address(uint160(bytes20(txn.sender))) !=
    address(uint160(bytes20(txn.liquidatee))),
    ERR_UNAUTHORIZED
);
```

This mirrors the pattern already used throughout the codebase (e.g., `feeTiers[address(uint160(bytes20(sender)))]`) and closes the bypass for all subaccount variants controlled by the same wallet. [5](#0-4) 

---

### Proof of Concept

1. Alice deposits collateral into two subaccounts: `alice:0` (healthy, quote balance) and `alice:1` (healthy initially).
2. Alice opens a large leveraged long perp position in `alice:1`.
3. The oracle price drops; `alice:1` falls below maintenance health.
4. Alice signs a `LiquidateSubaccount` transaction: `sender = alice:0`, `liquidatee = alice:1`, `productId = <perp>`, `amount = <full position>`.
5. The sequencer processes the transaction. On-chain: `txn.sender != txn.liquidatee` → `alice:0 != alice:1` → **passes**.
6. `alice:0` receives the perp position at `liquidationPrice` (e.g., 5% below oracle). Alice immediately closes it at oracle price via a normal order, netting the discount minus fees.
7. If `alice:1` is insolvent, `_finalizeSubaccount` is called and the insurance fund absorbs the residual bad debt. [6](#0-5) [7](#0-6)

### Citations

**File:** core/contracts/EndpointTx.sol (L73-76)
```text
        require(
            nonce == nonces[address(uint160(bytes20(sender)))]++,
            ERR_WRONG_NONCE
        );
```

**File:** core/contracts/ClearinghouseLiq.sol (L545-569)
```text
            (v.liquidationPriceX18, v.oraclePriceX18) = getLiqPriceX18(
                txn.productId,
                txn.amount
            );
            v.liquidationPayment = v.liquidationPriceX18.mul(txn.amount);
            v.liquidationFees = (v.oraclePriceX18 - v.liquidationPriceX18)
                .mul(LIQUIDATION_FEE_FRACTION)
                .mul(txn.amount);
            perpEngine.updateBalance(
                txn.productId,
                txn.liquidatee,
                -txn.amount,
                v.liquidationPayment
            );
            perpEngine.updateBalance(
                txn.productId,
                txn.sender,
                txn.amount,
                -v.liquidationPayment
            );
            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                txn.sender,
                -v.liquidationFees
            );
```

**File:** core/contracts/ClearinghouseLiq.sol (L598-607)
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
```

**File:** core/contracts/ClearinghouseLiq.sol (L619-627)
```text

        if (_finalizeSubaccount(txn, spotEngine, perpEngine)) {
            if (RiskHelper.isIsolatedSubaccount(txn.liquidatee)) {
                IOffchainExchange(
                    IEndpoint(getEndpoint()).getOffchainExchange()
                ).tryCloseIsolatedSubaccount(txn.liquidatee);
            }
            return;
        }
```

**File:** core/contracts/OffchainExchange.sol (L498-498)
```text
        uint32 feeTier = feeTiers[address(uint160(bytes20(sender)))];
```
