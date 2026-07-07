### Title
Same-Owner Cross-Subaccount Self-Liquidation Bypasses Anti-Self-Liquidation Guard — (File: `core/contracts/ClearinghouseLiq.sol`)

---

### Summary

`ClearinghouseLiq.liquidateSubaccountImpl` contains an anti-self-liquidation check that compares the full `bytes32` subaccount identifiers of the liquidator and liquidatee. Because a single owner address can control multiple subaccounts (each a different `bytes32`), the check is trivially bypassed: an attacker uses a second subaccount under the same address as the liquidator, passes the guard, and self-liquidates their own underwater position — capturing the liquidation discount while the insurance fund absorbs the deficit.

---

### Finding Description

In `ClearinghouseLiq.liquidateSubaccountImpl`, the only guard against self-liquidation is:

```solidity
require(txn.sender != txn.liquidatee, ERR_UNAUTHORIZED);
``` [1](#0-0) 

Nado subaccounts are `bytes32` values formed as `abi.encodePacked(ownerAddress, subaccountName)` — the first 20 bytes are the owner's address, the remaining 12 bytes are a user-chosen name. A single address can own an unlimited number of subaccounts with different names. The check above only prevents the **exact same** `bytes32` value from appearing on both sides; it does not compare the embedded owner addresses. [2](#0-1) 

An attacker controlling address `0xAttacker` can create:
- Subaccount A: `bytes32(abi.encodePacked(0xAttacker, "default000000"))` — the liquidatee
- Subaccount B: `bytes32(abi.encodePacked(0xAttacker, "subaccount2  "))` — the liquidator

`A != B` as `bytes32`, so the check passes. The attacker signs the `LiquidateSubaccount` transaction for `sender = B` with their own private key (valid, since `address(bytes20(B)) == 0xAttacker`), and the signature validation in `validateSignedTx` (called with `allowLinkedSigner = true`) accepts it. [3](#0-2) 

The `validateSignature` check confirms the recovered signer equals `address(uint160(bytes20(sender)))` — which is `0xAttacker` for subaccount B — so the attacker's signature is accepted. [4](#0-3) 

The sequencer, receiving a validly signed `LiquidateSubaccount` transaction, has no on-chain obligation to reject it. The transaction is included in a batch and processed on-chain, where the only guard (`txn.sender != txn.liquidatee`) passes.

---

### Impact Explanation

Once the liquidation executes, `_handleLiquidationPayment` transfers the liquidatee's assets to the liquidator at `liquidationPrice`, which is below oracle price. The liquidation fee goes to the insurance fund. [5](#0-4) 

If subaccount A has **negative equity** (liabilities exceed collateral), `updateQuoteFromInsurance` draws from the insurance fund to cover the shortfall: [6](#0-5) 

The attacker's net gain: assets received in B at a below-oracle discount, with the insurance fund absorbing A's deficit. This is a direct, repeatable drain of the protocol's insurance fund. The attack can be scaled to the insurance fund balance and position size limits.

---

### Likelihood Explanation

The attacker needs:
1. A genuinely unhealthy subaccount A (maintenance health < 0). This can arise from normal leveraged trading in volatile markets, or by deliberately taking maximum leverage and waiting for a small adverse price move.
2. A registered subaccount B under the same address (trivial — deposit any collateral to register it).
3. The sequencer to include the signed transaction. The sequencer processes valid signed transactions; there is no on-chain enforcement preventing it from including a same-owner cross-subaccount liquidation.

No price oracle manipulation, admin access, or sequencer compromise is required. The attacker only needs to control two subaccounts under the same address and have one become liquidatable — a realistic scenario in any leveraged trading protocol.

---

### Recommendation

Replace the subaccount-identity check with an owner-address check by comparing the first 20 bytes of each subaccount:

```solidity
require(bytes20(txn.sender) != bytes20(txn.liquidatee), ERR_UNAUTHORIZED);
```

This prevents any subaccount owned by the same address from acting as liquidator for another subaccount owned by that same address, regardless of the subaccount name suffix. [7](#0-6) 

---

### Proof of Concept

```
1. Attacker address: 0xAttacker

2. Register subaccount A:
   A = bytes32(abi.encodePacked(0xAttacker, bytes12("default000000")))
   Deposit collateral, open max-leverage long perp position.

3. Register subaccount B:
   B = bytes32(abi.encodePacked(0xAttacker, bytes12("subaccount2  ")))
   Deposit minimal collateral (just enough to pass requireSubaccount).

4. Wait for A's maintenance health < 0 (position goes underwater).

5. Attacker signs off-chain:
   LiquidateSubaccount {
       sender:          B,
       liquidatee:      A,
       productId:       <perp product>,
       isEncodedSpread: false,
       amount:          <full position size>,
       nonce:           <current nonce for B>
   }
   Signature: ECDSA signed by 0xAttacker (valid for subaccount B).

6. Submit signed transaction to sequencer.
   Sequencer includes it in next batch → submitTransactionsChecked.

7. On-chain execution in liquidateSubaccountImpl:
   - require(!isIsolatedSubaccount(B))  → passes (B is regular)
   - require(B != A)                    → passes (different bytes32)
   - require(isUnderMaintenance(A))     → passes (A is underwater)
   - _handleLiquidationPayment executes:
       A loses position at liquidationPrice (below oracle)
       B gains position at liquidationPrice
       Insurance fund covers A's negative quote balance

8. Result: 0xAttacker's subaccount B holds assets acquired at a discount;
   insurance fund is drained by A's deficit.
```

### Citations

**File:** core/contracts/ClearinghouseLiq.sol (L507-543)
```text
        } else if (engine == address(spotEngine)) {
            (v.liquidationPriceX18, v.oraclePriceX18) = getLiqPriceX18(
                txn.productId,
                txn.amount
            );

            v.liquidationPayment = v.liquidationPriceX18.mul(txn.amount);
            v.liquidationFees = (v.oraclePriceX18 - v.liquidationPriceX18)
                .mul(LIQUIDATION_FEE_FRACTION)
                .mul(txn.amount);

            spotEngine.updateBalance(
                txn.productId,
                txn.liquidatee,
                -txn.amount
            );

            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                txn.liquidatee,
                v.liquidationPayment
            );

            spotEngine.updateBalance(txn.productId, txn.sender, txn.amount);

            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                txn.sender,
                -v.liquidationPayment - v.liquidationFees
            );

            if (txn.amount < 0) {
                insurance = spotEngine.updateQuoteFromInsurance(
                    txn.liquidatee,
                    insurance
                );
            }
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

**File:** core/contracts/Endpoint.sol (L108-110)
```text
        bytes32 subaccount = bytes32(
            abi.encodePacked(msg.sender, subaccountName)
        );
```

**File:** core/contracts/EndpointTx.sol (L396-403)
```text
            if (signedTx.tx.sender != N_ACCOUNT) {
                validateSignedTx(
                    signedTx.tx.sender,
                    signedTx.tx.nonce,
                    transaction,
                    signedTx.signature,
                    true
                );
```

**File:** core/contracts/Verifier.sol (L296-303)
```text
    ) public pure {
        address recovered = ECDSA.recover(digest, signature);
        require(
            (recovered != address(0)) &&
                ((recovered == address(uint160(bytes20(sender)))) ||
                    (recovered == linkedSigner)),
            ERR_INVALID_SIGNATURE
        );
```
