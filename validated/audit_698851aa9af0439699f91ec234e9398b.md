### Title
Linked Signer Can Self-Perpetuate Authorization via `LinkSigner` Transaction — (`File: core/contracts/EndpointTx.sol`)

---

### Summary

In `EndpointTx.processTransactionImpl`, the sequencer-path `LinkSigner` transaction is validated with `allowLinkedSigner = true`. This means the currently registered linked signer can sign a new `LinkSigner` transaction to replace itself with a different address it controls — without any involvement from the subaccount owner. Because the only revocation path available to the owner goes through the slow-mode queue (3-day delay), a malicious or compromised linked signer can perpetually re-link to a fresh address before the owner's revocation is ever executed, making the linked signer authorization effectively irrevocable.

---

### Finding Description

`EndpointStorage.sol` stores a `mapping(bytes32 => address) internal linkedSigners` that maps each subaccount to a single authorized signer address. [1](#0-0) 

The linked signer is granted broad authority: it can sign `WithdrawCollateral`, `TransferQuote`, `LiquidateSubaccount`, `MintNlp`, `BurnNlp`, and other sensitive transactions on behalf of the subaccount owner. [2](#0-1) 

In `EndpointTx.processTransactionImpl`, the sequencer-path handler for `LinkSigner` calls `validateSignedTx` with `allowLinkedSigner = true`: [3](#0-2) 

`validateSignedTx` with `allowLinkedSigner = true` passes the current linked signer address to `verifier.validateSignature`, which accepts a signature from **either** the subaccount owner **or** the linked signer: [4](#0-3) 

This means the currently registered linked signer can produce a valid `SignedLinkSigner` transaction that replaces `linkedSigners[subaccount]` with any address it chooses — including a fresh address it controls.

The only revocation path available to the owner is the slow-mode queue, which imposes a hardcoded 3-day delay: [5](#0-4) 

The slow-mode `LinkSigner` handler uses `validateSender`, which correctly requires the actual owner: [5](#0-4) 

But the fast (sequencer) path does not. The 3-day window gives a malicious linked signer ample time to submit a new fast-path `LinkSigner` transaction through the sequencer, re-linking to a new address before the owner's slow-mode revocation is executed.

---

### Impact Explanation

A compromised or malicious linked signer can:

1. Sign a `LinkSigner` transaction pointing to a fresh address it controls.
2. The sequencer processes this fast-path transaction immediately, updating `linkedSigners[subaccount]` to the new address.
3. The owner's slow-mode revocation (3-day delay) only revokes the new address, not the original.
4. The attacker repeats step 1–3 indefinitely, maintaining persistent signing authority over the subaccount.

With persistent linked signer control, the attacker can sign `WithdrawCollateral` transactions (which also accept `allowLinkedSigner = true`) to drain the subaccount's collateral to the owner's address (the default destination when `sendTo == address(0)`), or manipulate positions via `MatchOrders`. [6](#0-5) 

The corrupted state is: `linkedSigners[victim_subaccount]` permanently controlled by the attacker, enabling unauthorized collateral withdrawals and position manipulation.

---

### Likelihood Explanation

The trigger requires a linked signer key to be compromised (e.g., an API key for a trading bot stored on a server). This is a realistic and common scenario in DEX protocols that support linked signers for automated trading. Once the key is compromised, the attack is fully on-chain, requires no further social engineering, and is self-sustaining. The 3-day slow-mode delay makes the owner's revocation race structurally unwinnable against a sequencer-submitted fast-path re-link.

---

### Recommendation

The `LinkSigner` transaction in the sequencer path should be validated with `allowLinkedSigner = false`, so that only the subaccount owner's key can authorize a change to the linked signer:

```solidity
// EndpointTx.processTransactionImpl — LinkSigner branch
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false  // was: true — linked signer must NOT be able to re-link itself
);
```

This mirrors the slow-mode path, which already correctly enforces owner-only authorization via `validateSender`.

---

### Proof of Concept

1. Alice owns subaccount `alice_sub` and sets a linked signer `bot_key` for automated trading via a `LinkSigner` slow-mode transaction.
2. `bot_key` is compromised by an attacker.
3. Alice discovers the compromise and submits a slow-mode `LinkSigner` transaction setting `signer = bytes32(0)` to revoke `bot_key`. This enters the slow-mode queue with a 3-day delay.
4. The attacker, holding `bot_key`, immediately signs a fast-path `LinkSigner` transaction (valid because `allowLinkedSigner = true`) setting `signer = attacker_key2`. The sequencer processes this within seconds, updating `linkedSigners[alice_sub] = attacker_key2`.
5. Alice's slow-mode revocation executes after 3 days, setting `linkedSigners[alice_sub] = address(0)` — but this only clears `attacker_key2`, not `bot_key` (already replaced).
6. The attacker repeats step 4 with `attacker_key2` before Alice's next revocation, re-linking to `attacker_key3`. This loop continues indefinitely.
7. At any point during the loop, the attacker signs a `WithdrawCollateral` transaction from `alice_sub` (accepted because `allowLinkedSigner = true`) to drain Alice's collateral. [3](#0-2) [4](#0-3) [1](#0-0)

### Citations

**File:** core/contracts/EndpointStorage.sol (L50-50)
```text
    mapping(bytes32 => address) internal linkedSigners;
```

**File:** core/contracts/Verifier.sol (L291-303)
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
```

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
