### Title
Linked Signer Can Overwrite Its Own Authorization, Enabling Subaccount Takeover - (File: `core/contracts/EndpointTx.sol`)

---

### Summary

The `LinkSigner` fast-path transaction in `EndpointTx.sol` is validated with `allowLinkedSigner=true`, meaning the currently linked signer can sign a `LinkSigner` transaction to replace itself with any arbitrary address. This allows a compromised linked signer to redirect subaccount control to an attacker-controlled address, enabling full asset drainage — an exact structural analog to the external report's "privileged role mutates any user's state without owner consent."

---

### Finding Description

In `EndpointTx.sol` at lines 576–590, the `LinkSigner` fast-path transaction is processed as follows:

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
        true                          // ← allowLinkedSigner = true
    );
    linkedSigners[signedTx.tx.sender] = address(
        uint160(bytes20(signedTx.tx.signer))
    );
}
``` [1](#0-0) 

`validateSignedTx` with `allowLinkedSigner=true` passes the current linked signer to `verifier.validateSignature`, which accepts a signature from **either** the subaccount owner address **or** the linked signer:

```solidity
address recovered = ECDSA.recover(digest, signature);
require(
    (recovered != address(0)) &&
        ((recovered == address(uint160(bytes20(sender)))) ||
            (recovered == linkedSigner)),
    ERR_INVALID_SIGNATURE
);
``` [2](#0-1) 

This means the **currently linked signer** can sign a `LinkSigner` transaction to overwrite `linkedSigners[subaccount]` with any address it chooses — including an attacker-controlled address.

The slow-mode path for the same transaction type correctly restricts this to the subaccount owner only via `validateSender(txn.sender, sender)`:

```solidity
} else if (txType == IEndpoint.TransactionType.LinkSigner) {
    IEndpoint.LinkSigner memory txn = abi.decode(...);
    validateSender(txn.sender, sender);   // owner-only check
    requireSubaccount(txn.sender);
    linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
}
``` [3](#0-2) 

The asymmetry between the two paths confirms the fast-path `allowLinkedSigner=true` is unintended.

---

### Impact Explanation

Once the linked signer overwrites `linkedSigners[subaccount]` with an attacker-controlled address, the attacker inherits all linked-signer permissions. Critically, `WithdrawCollateral` (fast path) passes `allowLinkedSigner=true` unconditionally:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    true                   // linked signer accepted for withdrawals
);
``` [4](#0-3) 

The attacker can therefore sign `WithdrawCollateral` transactions to drain all collateral from the victim's subaccount. The same applies to `MintNlp`, `BurnNlp`, `TransferQuote`, and `LiquidateSubaccount`, all of which also pass `allowLinkedSigner=true`.

**Corrupted state delta**: `linkedSigners[victim_subaccount]` is overwritten from the legitimate hot-wallet address to an attacker-controlled address, followed by full collateral balance drain via `spotEngine.updateBalance`.

---

### Likelihood Explanation

Linked signers are by design hot wallets or API keys — roles with elevated exposure to compromise compared to cold-wallet subaccount owners. A phished API key, leaked environment variable, or compromised frontend session is sufficient to trigger this path. No admin key, governance capture, or sequencer compromise is required. The attacker only needs the current nonce (publicly readable from `nonces[address]`) and the compromised linked signer's private key.

---

### Recommendation

Change `allowLinkedSigner` to `false` for the `LinkSigner` fast-path transaction in `EndpointTx.sol`:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // only subaccount owner may change the linked signer
);
```

This aligns the fast path with the slow-mode path, which already enforces owner-only authorization for `LinkSigner`.

---

### Proof of Concept

1. Alice (cold wallet `0xAlice`) creates subaccount `alice_subaccount` and sets Bob (`0xBob`) as her linked signer via a `LinkSigner` slow-mode transaction.
2. An attacker compromises Bob's private key (e.g., via phishing or leaked `.env`).
3. The attacker reads the current nonce for `alice_subaccount` from `nonces[0xAlice]`.
4. The attacker uses Bob's key to sign a fast-path `LinkSigner` transaction with `sender = alice_subaccount`, `signer = 0xAttacker`, and the correct nonce.
5. The sequencer processes the transaction; `validateSignedTx(..., true)` accepts Bob's signature because `getLinkedSigner(alice_subaccount) == 0xBob`.
6. `linkedSigners[alice_subaccount]` is overwritten to `0xAttacker`.
7. The attacker signs `WithdrawCollateral` transactions using `0xAttacker`'s key, draining all of Alice's collateral balances.
8. Alice's subaccount is emptied before she can revoke the linked signer. [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** core/contracts/EndpointTx.sol (L418-424)
```text
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
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
