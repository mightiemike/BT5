### Title
Linked Signer Can Unilaterally Replace Itself Without Subaccount Owner Consent — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

The `LinkSigner` fast-path transaction in `EndpointTx.processTransactionImpl` is validated with `allowLinkedSigner = true`. This permits the **currently linked signer** (a session key) to sign and submit a new `LinkSigner` transaction, replacing itself with any arbitrary address — without any signature or consent from the subaccount owner. This is a direct analog to the "seller's signature not required" class: a delegated party can mutate a critical authorization state on behalf of the principal without the principal's knowledge.

---

### Finding Description

In `EndpointTx.processTransactionImpl`, the `LinkSigner` transaction branch is:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    true   // allowLinkedSigner
);
linkedSigners[signedTx.tx.sender] = address(
    uint160(bytes20(signedTx.tx.signer))
);
``` [1](#0-0) 

The `allowLinkedSigner = true` flag is forwarded through `validateSignature` into `Verifier.validateSignature`, which accepts the signature as valid if it comes from **either** the subaccount owner **or** the currently registered linked signer:

```solidity
require(
    (recovered != address(0)) &&
        ((recovered == address(uint160(bytes20(sender)))) ||
            (recovered == linkedSigner)),
    ERR_INVALID_SIGNATURE
);
``` [2](#0-1) 

Because `LinkSigner` itself uses `allowLinkedSigner = true`, the currently linked signer can craft and sign a new `LinkSigner` transaction that writes any arbitrary address into `linkedSigners[subaccount]`. The subaccount owner's signature is never required for this mutation.

Compare this to all other sensitive transactions: `WithdrawCollateral`, `MintNlp`, `BurnNlp`, and `TransferQuote` also use `allowLinkedSigner = true`, meaning the newly installed malicious signer immediately inherits full authority to drain the account. [3](#0-2) 

The `getLinkedSigner` function also propagates the linked signer from a parent subaccount to all of its isolated subaccounts, so a single `LinkSigner` mutation affects the entire isolated subaccount tree. [4](#0-3) 

---

### Impact Explanation

A compromised or malicious linked signer (session key) can:

1. Sign a `LinkSigner` transaction naming an attacker-controlled address as the new signer.
2. The sequencer processes this without any subaccount-owner signature.
3. The attacker's address is now the linked signer and can sign `WithdrawCollateral`, `TransferQuote`, `BurnNlp`, and order transactions.
4. All collateral in the subaccount (and its isolated children) can be drained.

The subaccount owner loses the ability to simply "stop using" the session key to neutralize it, because the session key has already silently rotated to an address the owner does not control.

---

### Likelihood Explanation

Linked signers are explicitly designed as lower-trust session keys (e.g., browser-held keys, hot wallets, API keys). They are the most likely component to be leaked or compromised. The attack requires only that the linked signer is compromised — no admin access, no sequencer compromise, and no governance capture is needed. The sequencer will process the transaction as a normal fast-path batch entry.

---

### Recommendation

Change `allowLinkedSigner` to `false` for the `LinkSigner` transaction type:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false  // only the subaccount owner may change the linked signer
);
``` [5](#0-4) 

This ensures that only the subaccount owner (the principal) can authorize a change to the linked signer, directly mirroring the fix applied in the referenced report: requiring the seller's own signature over the authorization action.

---

### Proof of Concept

1. Alice links a session key `SK` to her subaccount via a legitimate `LinkSigner` transaction. `linkedSigners[alice_subaccount] = SK`.
2. An attacker compromises `SK`.
3. The attacker uses `SK` to sign a new `LinkSigner` transaction: `{ sender: alice_subaccount, signer: attacker_address, nonce: current_nonce }`.
4. The sequencer includes this in a batch. `validateSignedTx` passes because `recovered == SK == linkedSigner`.
5. `linkedSigners[alice_subaccount]` is now set to `attacker_address`.
6. The attacker signs a `WithdrawCollateral` transaction from `attacker_address`, which passes `validateSignature` as the new linked signer.
7. Alice's entire collateral balance is withdrawn to the attacker. [1](#0-0) [6](#0-5)

### Citations

**File:** core/contracts/EndpointTx.sol (L143-157)
```text
    function getLinkedSigner(bytes32 subaccount)
        public
        view
        virtual
        returns (address)
    {
        return
            RiskHelper.isIsolatedSubaccount(subaccount)
                ? linkedSigners[
                    IOffchainExchange(offchainExchange).getParentSubaccount(
                        subaccount
                    )
                ]
                : linkedSigners[subaccount];
    }
```

**File:** core/contracts/EndpointTx.sol (L413-436)
```text
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
            IEndpoint.SignedWithdrawCollateral memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedWithdrawCollateral)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            chargeFee(
                signedTx.tx.sender,
                spotEngine.getConfig(signedTx.tx.productId).withdrawFeeX18,
                signedTx.tx.productId
            );
            clearinghouse.withdrawCollateral(
                signedTx.tx.sender,
                signedTx.tx.productId,
                signedTx.tx.amount,
                address(0),
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

**File:** core/contracts/Verifier.sol (L291-304)
```text
    function validateSignature(
        bytes32 sender,
        address linkedSigner,
        bytes32 digest,
        bytes memory signature
    ) public pure {
        address recovered = ECDSA.recover(digest, signature);
        require(
            (recovered != address(0)) &&
                ((recovered == address(uint160(bytes20(sender)))) ||
                    (recovered == linkedSigner)),
            ERR_INVALID_SIGNATURE
        );
    }
```
