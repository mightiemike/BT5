### Title
Linked Signer Can Overwrite Its Own Authority via `LinkSigner` Transaction — (`File: core/contracts/EndpointTx.sol`)

---

### Summary

In `EndpointTx.processTransactionImpl`, the `LinkSigner` transaction type is validated with `allowLinkedSigner = true`. This means the **currently registered linked signer** of a subaccount can authorize a `LinkSigner` transaction to replace itself with any arbitrary address — including an attacker-controlled one. The linked signer should only be permitted to sign trading operations on behalf of the subaccount owner, not to mutate the signing authority itself.

---

### Finding Description

The sequencer-path handler for `LinkSigner` in `processTransactionImpl` passes `true` as the `allowLinkedSigner` argument to `validateSignedTx`:

```solidity
// EndpointTx.sol lines 576–590
} else if (txType == IEndpoint.TransactionType.LinkSigner) {
    IEndpoint.SignedLinkSigner memory signedTx = abi.decode(
        transaction[1:],
        (IEndpoint.SignedLinkSigner)
    );
    validateSignedTx(
        signedTx.tx.sender,
        signedTx.tx.nonce,
        transaction,
        signedTx.signature,
        true          // ← allowLinkedSigner
    );
    linkedSigners[signedTx.tx.sender] = address(
        uint160(bytes20(signedTx.tx.signer))
    );
}
``` [1](#0-0) 

`validateSignedTx` delegates to `validateSignature`, which passes `getLinkedSigner(sender)` as the accepted signer when `allowLinkedSigner` is `true`:

```solidity
// EndpointTx.sol lines 172–184
verifier.validateSignature(
    sender,
    allowLinkedSigner ? getLinkedSigner(sender) : address(0),
    digest,
    signature
);
``` [2](#0-1) 

`Verifier.validateSignature` accepts the signature if it recovers to either the subaccount owner address **or** the linked signer:

```solidity
// Verifier.sol lines 297–303
address recovered = ECDSA.recover(digest, signature);
require(
    (recovered != address(0)) &&
        ((recovered == address(uint160(bytes20(sender)))) ||
            (recovered == linkedSigner)),
    ERR_INVALID_SIGNATURE
);
``` [3](#0-2) 

Therefore, the current linked signer can produce a valid `SignedLinkSigner` transaction that replaces `linkedSigners[subaccount]` with any address it chooses.

By contrast, the slow-mode path for `LinkSigner` correctly restricts the operation to the subaccount owner via `validateSender`:

```solidity
// EndpointTx.sol lines 232–239
validateSender(txn.sender, sender);   // msg.sender must equal subaccount owner
requireSubaccount(txn.sender);
linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
``` [4](#0-3) 

The fast path (sequencer path) lacks this restriction, creating an asymmetry where the linked signer can self-escalate through the normal sequencer flow.

---

### Impact Explanation

Once the linked signer replaces itself with an attacker-controlled address, the attacker gains full signing authority over the victim's subaccount. All subsequent sequencer-path transactions that accept `allowLinkedSigner = true` — including `WithdrawCollateral`, `WithdrawCollateralV2`, `TransferQuote`, `MintNlp`, `BurnNlp`, and `LiquidateSubaccount` — can be signed by the attacker. The attacker can drain the subaccount's collateral balances entirely.

The corrupted state delta is: `linkedSigners[victimSubaccount]` is overwritten from a legitimate hot-wallet address to an attacker-controlled address, permanently transferring signing authority over all subaccount assets.

---

### Likelihood Explanation

The linked signer role is commonly assigned to a hot wallet or automated trading bot. Any compromise of that key — through phishing, key leakage, or a malicious service provider — immediately enables this escalation. The attacker does not need any special on-chain privileges; they only need to submit a single `LinkSigner` transaction through the sequencer. The sequencer has no reason to reject it because the signature is cryptographically valid.

---

### Recommendation

Change `allowLinkedSigner` to `false` for the `LinkSigner` transaction type in `processTransactionImpl`. Only the subaccount owner (the address embedded in `sender`) should be permitted to change the linked signer. This mirrors the correct behavior already implemented in the slow-mode path.

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // ← only the subaccount owner may change the linked signer
);
```

---

### Proof of Concept

1. Alice owns subaccount `aliceSub` and has set `linkedSigners[aliceSub] = botKey`.
2. Attacker compromises `botKey`.
3. Attacker constructs a `SignedLinkSigner` transaction:
   - `sender = aliceSub`
   - `signer = attackerAddress`
   - `nonce = nonces[aliceAddress]`
   - Signs with `botKey`
4. Attacker submits the transaction to the sequencer. `validateSignedTx` passes because `botKey == getLinkedSigner(aliceSub)` and `allowLinkedSigner = true`.
5. `linkedSigners[aliceSub]` is now set to `attackerAddress`.
6. Attacker constructs a `SignedWithdrawCollateralV2` transaction:
   - `sender = aliceSub`, `productId = quoteProductId`, `amount = fullBalance`, `sendTo = attackerEOA`
   - Signs with `attackerAddress`
7. Sequencer processes it; `validateSignedTx` passes because `attackerAddress == getLinkedSigner(aliceSub)`.
8. `clearinghouse.withdrawCollateral` transfers Alice's full collateral balance to `attackerEOA`. [5](#0-4)

### Citations

**File:** core/contracts/EndpointTx.sol (L172-184)
```text
    function validateSignature(
        bytes32 sender,
        bytes32 digest,
        bytes memory signature,
        bool allowLinkedSigner
    ) internal virtual {
        verifier.validateSignature(
            sender,
            allowLinkedSigner ? getLinkedSigner(sender) : address(0),
            digest,
            signature
        );
    }
```

**File:** core/contracts/EndpointTx.sol (L232-239)
```text
        } else if (txType == IEndpoint.TransactionType.LinkSigner) {
            IEndpoint.LinkSigner memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.LinkSigner)
            );
            validateSender(txn.sender, sender);
            requireSubaccount(txn.sender);
            linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
```

**File:** core/contracts/EndpointTx.sol (L437-465)
```text
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateralV2) {
            IEndpoint.SignedWithdrawCollateralV2 memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedWithdrawCollateralV2)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                signedTx.tx.sendTo == address(0)
            );
            int128 currentFeeX18 = spotEngine
                .getConfig(signedTx.tx.productId)
                .withdrawFeeX18;
            require(signedTx.feeX18 >= 0);
            require(signedTx.feeX18 <= currentFeeX18);
            chargeFee(
                signedTx.tx.sender,
                signedTx.feeX18,
                signedTx.tx.productId
            );
            clearinghouse.withdrawCollateral(
                signedTx.tx.sender,
                signedTx.tx.productId,
                signedTx.tx.amount,
                signedTx.tx.sendTo,
                nSubmissions
            );
```

**File:** core/contracts/EndpointTx.sol (L576-590)
```text
        } else if (txType == IEndpoint.TransactionType.LinkSigner) {
            IEndpoint.SignedLinkSigner memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedLinkSigner)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            linkedSigners[signedTx.tx.sender] = address(
                uint160(bytes20(signedTx.tx.signer))
            );
```

**File:** core/contracts/Verifier.sol (L297-303)
```text
        address recovered = ECDSA.recover(digest, signature);
        require(
            (recovered != address(0)) &&
                ((recovered == address(uint160(bytes20(sender)))) ||
                    (recovered == linkedSigner)),
            ERR_INVALID_SIGNATURE
        );
```
