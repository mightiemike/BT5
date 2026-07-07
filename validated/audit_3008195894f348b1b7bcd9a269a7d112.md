### Title
Linked Signer Can Overwrite `linkedSigners` Mapping Without Subaccount Owner Consent — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

In `EndpointTx.processTransactionImpl`, the `LinkSigner` transaction type is validated with `allowLinkedSigner = true`. This means the **existing linked signer** — not just the subaccount owner — can sign a new `LinkSigner` transaction to replace the linked signer with any arbitrary address. A malicious or compromised linked signer can silently install an attacker-controlled address as the new linked signer, which then has full signing authority over the subaccount, including the ability to sign withdrawals.

---

### Finding Description

In `processTransactionImpl`, the `LinkSigner` branch calls `validateSignedTx` with `allowLinkedSigner = true`: [1](#0-0) 

`validateSignedTx` delegates to `validateSignature`, which passes `getLinkedSigner(sender)` as the accepted alternate signer: [2](#0-1) 

`verifier.validateSignature` then accepts the signature if it comes from **either** the subaccount owner address **or** the currently registered linked signer: [3](#0-2) 

Because the `LinkSigner` transaction type itself is processed with `allowLinkedSigner = true`, the existing linked signer can sign a `LinkSigner` payload that overwrites `linkedSigners[subaccount]` with any address — without the subaccount owner's knowledge or consent: [4](#0-3) 

This is the direct analog to the reported bug: just as `OmoRouter.registerAccount` fails to check that `msg.sender` is the owner of the account being registered, Nado's `LinkSigner` fast path fails to restrict the mutation of `linkedSigners` to the subaccount owner only.

---

### Impact Explanation

Once the attacker-controlled address is installed as the new linked signer, it can sign `WithdrawCollateral` transactions on behalf of the victim subaccount, since that path also uses `allowLinkedSigner = true`: [5](#0-4) 

This enables full collateral theft from the victim subaccount. The corrupted state is `linkedSigners[subaccount]` — a mapping that gates signing authority for all fast-path transactions including withdrawals, order matching, and quote transfers.

**Impact: Medium** — collateral theft is possible once the linked signer is replaced.

---

### Likelihood Explanation

Linked signers are routinely set to automated bots, market-making services, or third-party trading infrastructure. Any of these can be compromised or act maliciously. The attack requires no special privileges beyond already being the registered linked signer for a target subaccount.

**Likelihood: Medium** — realistic in any deployment where users delegate signing to external services.

---

### Recommendation

Change `allowLinkedSigner` to `false` for the `LinkSigner` transaction type in `processTransactionImpl`. Only the subaccount owner (the address embedded in the first 20 bytes of the `bytes32` subaccount) should be permitted to change the linked signer:

```solidity
// Before (vulnerable):
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    true   // ← allows existing linked signer to overwrite itself
);

// After (fixed):
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false  // ← only subaccount owner may change the linked signer
);
```

---

### Proof of Concept

1. Alice owns `alice_sub` and sets `bob_address` as her linked signer via a legitimate `LinkSigner` transaction.
2. Bob (malicious) constructs a `SignedLinkSigner` payload: `sender = alice_sub`, `signer = mallory_address`, `nonce = current_nonce`, signed with Bob's private key.
3. Bob submits this to the sequencer. The sequencer calls `processTransactionImpl`.
4. `validateSignedTx(..., true)` is called. `getLinkedSigner(alice_sub)` returns `bob_address`. Bob's signature is valid → passes.
5. `linkedSigners[alice_sub] = mallory_address` is written.
6. Mallory constructs a `SignedWithdrawCollateral` payload: `sender = alice_sub`, signed with Mallory's key.
7. `validateSignedTx(..., true)` accepts Mallory's signature (now the registered linked signer).
8. Alice's collateral is withdrawn to Mallory's address. [1](#0-0) [5](#0-4)

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
