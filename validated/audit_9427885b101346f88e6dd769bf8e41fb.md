### Title
Linked Signer Can Overwrite `linkedSigners` to Arbitrary Address — (`core/contracts/EndpointTx.sol`)

---

### Summary

In `EndpointTx.processTransactionImpl`, the `LinkSigner` transaction type is validated with `allowLinkedSigner = true`. This means the **current linked signer** of a subaccount can sign and submit a new `LinkSigner` transaction to replace itself with any arbitrary address — without the subaccount owner's involvement. This is a direct analog to the external report's class: a mutable protocol parameter (here, the authorized signer address) can be changed to an arbitrary value by a party that should not hold that authority, enabling account takeover.

---

### Finding Description

In `processTransactionImpl`, the `LinkSigner` branch reads:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    true   // allowLinkedSigner = true
);
linkedSigners[signedTx.tx.sender] = address(
    uint160(bytes20(signedTx.tx.signer))
);
``` [1](#0-0) 

`validateSignedTx` with `allowLinkedSigner = true` delegates to `validateSignature`, which passes `getLinkedSigner(sender)` as the permitted signer:

```solidity
function validateSignature(..., bool allowLinkedSigner) internal virtual {
    verifier.validateSignature(
        sender,
        allowLinkedSigner ? getLinkedSigner(sender) : address(0),
        digest,
        signature
    );
}
``` [2](#0-1) 

There is no check that the new `signer` value in the transaction is the subaccount owner, a previously-authorized address, or any constrained set. The linked signer can set `signedTx.tx.signer` to any `bytes32` value, and the contract will write `address(uint160(bytes20(signedTx.tx.signer)))` directly into `linkedSigners[signedTx.tx.sender]`. [3](#0-2) 

Compare this to the slow-mode path for `LinkSigner`, which correctly requires `validateSender(txn.sender, sender)` — i.e., the Ethereum `msg.sender` must match the subaccount owner — but the fast-path (`processTransactionImpl`) has no equivalent ownership check and instead permits the linked signer to self-replace. [4](#0-3) 

---

### Impact Explanation

Once the linked signer is replaced with an attacker-controlled address, the attacker gains full signing authority over the subaccount for every transaction type that passes `allowLinkedSigner = true`. This includes:

- `MintNlp` / `BurnNlp` — burn the victim's NLP position, extracting quote value
- `TransferQuote` — transfer quote balance to another subaccount sharing the same 20-byte address prefix (e.g., a different subaccount name under the same wallet)
- `LiquidateSubaccount` — initiate liquidations that benefit the attacker's own positions
- A subsequent `LinkSigner` — the attacker can keep rotating the signer, making revocation a persistent race condition [5](#0-4) 

The subaccount owner cannot atomically revoke the linked signer because all transactions route through the sequencer; the attacker can re-submit a replacement `LinkSigner` transaction before the owner's revocation is processed.

---

### Likelihood Explanation

Linked signers are the standard mechanism for automated trading bots and API keys in Nado. Private key leakage for hot-wallet bots is a realistic and common operational risk. A single leaked bot key is sufficient to trigger this attack with no other preconditions. The attacker does not need owner-level access, governance capture, or any external dependency — only the ability to submit a signed transaction to the sequencer, which is the normal user flow.

---

### Recommendation

Change `allowLinkedSigner` to `false` for `LinkSigner` transactions in `processTransactionImpl`:

```diff
- validateSignedTx(
-     signedTx.tx.sender,
-     signedTx.tx.nonce,
-     transaction,
-     signedTx.signature,
-     true
- );
+ validateSignedTx(
+     signedTx.tx.sender,
+     signedTx.tx.nonce,
+     transaction,
+     signedTx.signature,
+     false   // only the subaccount owner may change the linked signer
+ );
``` [6](#0-5) 

This mirrors the slow-mode path, which already enforces `validateSender(txn.sender, sender)` — ensuring only the subaccount owner can authorize a signer change. [4](#0-3) 

---

### Proof of Concept

1. User A sets linked signer to trading bot address B via a `LinkSigner` transaction.
2. Bot B's private key is leaked (or Bot B is malicious).
3. Attacker uses B's key to sign a new `LinkSigner` transaction: `{ sender: A's subaccount, signer: attacker_address_C, nonce: current_nonce }`.
4. The sequencer includes this transaction; `processTransactionImpl` validates it — `allowLinkedSigner = true` so B's signature is accepted — and writes `linkedSigners[A's subaccount] = C`.
5. Attacker C now has full signing authority. C signs a `BurnNlp` transaction to liquidate A's NLP position, receiving quote tokens into A's subaccount, then signs a `TransferQuote` to move quote to another subaccount under the same Ethereum address that C controls.
6. If A attempts to revoke by submitting a new `LinkSigner`, C immediately re-submits a counter-transaction; the sequencer ordering determines the outcome, and C can sustain this indefinitely. [1](#0-0)

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

**File:** core/contracts/EndpointTx.sol (L534-590)
```text
        } else if (txType == IEndpoint.TransactionType.MintNlp) {
            IEndpoint.SignedMintNlp memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedMintNlp)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            chargeFee(signedTx.tx.sender, HEALTHCHECK_FEE);
            priceX18[NLP_PRODUCT_ID] = signedTx.oraclePriceX18;
            clearinghouse.mintNlp(
                signedTx.tx,
                signedTx.oraclePriceX18,
                nlpPools,
                signedTx.nlpPoolRebalanceX18
            );
        } else if (txType == IEndpoint.TransactionType.BurnNlp) {
            IEndpoint.SignedBurnNlp memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedBurnNlp)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            chargeFee(signedTx.tx.sender, HEALTHCHECK_FEE);
            priceX18[NLP_PRODUCT_ID] = signedTx.oraclePriceX18;
            clearinghouse.burnNlp(
                signedTx.tx,
                signedTx.oraclePriceX18,
                nlpPools,
                signedTx.nlpPoolRebalanceX18
            );
        } else if (txType == IEndpoint.TransactionType.ManualAssert) {
            clearinghouse.manualAssert(transaction);
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
