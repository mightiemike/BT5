### Title
Linked Signer Can Overwrite Its Own Delegation via `LinkSigner` — (`core/contracts/EndpointTx.sol`)

---

### Summary

In `EndpointTx.sol`, the fast-path (sequencer-submitted) handler for `TransactionType.LinkSigner` calls `validateSignedTx` with `allowLinkedSigner = true`. This permits the **current linked signer** — a delegated, limited-privilege entity — to sign and submit a `LinkSigner` transaction that overwrites `linkedSigners[subaccount]` with any address it chooses. The subaccount owner's exclusive right to control who holds the linked signer slot is broken.

---

### Finding Description

The fast-path `LinkSigner` handler in `EndpointTx.sol` at lines 576–590:

```solidity
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
        true          // ← allowLinkedSigner = true
    );
    linkedSigners[signedTx.tx.sender] = address(
        uint160(bytes20(signedTx.tx.signer))
    );
``` [1](#0-0) 

`validateSignedTx` with `allowLinkedSigner = true` delegates to `validateSignature`, which passes `getLinkedSigner(sender)` as the permitted alternate signer:

```solidity
verifier.validateSignature(
    sender,
    allowLinkedSigner ? getLinkedSigner(sender) : address(0),
    digest,
    signature
);
``` [2](#0-1) 

This means a valid signature from the **current linked signer** satisfies the check, and the transaction then writes `linkedSigners[signedTx.tx.sender] = <attacker-chosen address>`.

**Contrast with the slow-mode path** (lines 232–239), which uses `validateSender(txn.sender, sender)` — verifying the actual Ethereum `msg.sender` matches the subaccount owner address. The slow-mode path correctly restricts `LinkSigner` mutations to the subaccount owner only. [3](#0-2) 

---

### Impact Explanation

A linked signer can:

1. **Replace itself** with a new address it controls — persisting access even after the subaccount owner believes they have revoked it by submitting a slow-mode `LinkSigner` with `signer = 0`.
2. **Set an entirely new linked signer** the owner never authorized, granting a third party the ability to sign all `allowLinkedSigner = true` transactions (withdrawals, transfers, order matching, NLP mint/burn) on behalf of the victim subaccount.
3. **Race the owner's revocation**: because the sequencer processes fast-path transactions before slow-mode ones, the linked signer can submit a replacement `LinkSigner` transaction through the sequencer to re-establish access before the owner's slow-mode revocation is processed.

The corrupted state is `linkedSigners[subaccount]` — the access-control slot that governs who may sign on behalf of the subaccount for all privileged fast-path operations.

---

### Likelihood Explanation

Any address that has ever been granted the linked signer role for a subaccount can trigger this. No admin key, governance capture, or external dependency is required. The attacker only needs a valid linked signer key and access to the sequencer's public submission endpoint (`submitTransactions`). This is a realistic scenario for any user who has delegated signing to a third-party service (e.g., a trading bot or API key).

---

### Recommendation

Change `allowLinkedSigner` to `false` for the `LinkSigner` transaction type in the fast-path handler, consistent with the slow-mode path which requires the actual subaccount owner to authorize linked signer changes:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // only the subaccount owner may change the linked signer
);
``` [4](#0-3) 

---

### Proof of Concept

1. Alice sets `linkedSigners[alice_subaccount] = bot_address` via a legitimate slow-mode `LinkSigner` transaction.
2. `bot_address` (the linked signer) crafts a `SignedLinkSigner` with:
   - `tx.sender = alice_subaccount`
   - `tx.signer = attacker_new_address` (an address the bot controls)
   - `tx.nonce = current_nonce`
   - `signature` = signed by `bot_address`
3. Bot submits this to the sequencer via `submitTransactions`.
4. The sequencer calls `processTransactionImpl` → `LinkSigner` branch.
5. `validateSignedTx(..., true)` passes because `getLinkedSigner(alice_subaccount) == bot_address` and the signature is valid.
6. `linkedSigners[alice_subaccount]` is now `attacker_new_address`.
7. Alice submits a slow-mode `LinkSigner` with `signer = 0` to revoke. The sequencer processes the fast-path replacement first, so `attacker_new_address` is now the linked signer.
8. `attacker_new_address` can now sign withdrawals, transfers, and order transactions on Alice's behalf.

### Citations

**File:** core/contracts/EndpointTx.sol (L178-183)
```text
        verifier.validateSignature(
            sender,
            allowLinkedSigner ? getLinkedSigner(sender) : address(0),
            digest,
            signature
        );
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
