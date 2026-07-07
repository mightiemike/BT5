### Title
Linked Signer Can Unilaterally Replace Itself Without Subaccount Owner Consent — (`File: core/contracts/EndpointTx.sol`)

---

### Summary

The fast-path `LinkSigner` handler in `EndpointTx.processTransactionImpl` accepts a signature from the **currently registered linked signer** to overwrite `linkedSigners[subaccount]` with any new address. This allows a linked signer to silently replace itself — or install an attacker-controlled address — without the subaccount owner's knowledge or approval. The new linked signer can then sign `WithdrawCollateral` and other privileged transactions to drain the subaccount's collateral.

---

### Finding Description

In `EndpointTx.sol`, the fast-path sequencer handler for `LinkSigner` transactions is:

```solidity
// EndpointTx.sol L576-590
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
        true              // <-- allowLinkedSigner = true
    );
    linkedSigners[signedTx.tx.sender] = address(
        uint160(bytes20(signedTx.tx.signer))
    );
}
```

`validateSignedTx` with `allowLinkedSigner = true` resolves to `validateSignature`, which calls `verifier.validateSignature` passing `getLinkedSigner(sender)` as the accepted alternate signer:

```solidity
// EndpointTx.sol L172-183
function validateSignature(..., bool allowLinkedSigner) internal virtual {
    verifier.validateSignature(
        sender,
        allowLinkedSigner ? getLinkedSigner(sender) : address(0),
        digest,
        signature
    );
}
```

`verifier.validateSignature` accepts the signature if it recovers to **either** the subaccount owner address **or** the linked signer:

```solidity
// Verifier.sol L297-303
address recovered = ECDSA.recover(digest, signature);
require(
    (recovered != address(0)) &&
        ((recovered == address(uint160(bytes20(sender)))) ||
            (recovered == linkedSigner)),
    ERR_INVALID_SIGNATURE
);
```

This means the **currently registered linked signer** can sign a `LinkSigner` transaction that writes any new address into `linkedSigners[subaccount]` — completely replacing itself without the subaccount owner's involvement.

**Contrast with the slow-mode path**, which correctly restricts `LinkSigner` to the subaccount owner:

```solidity
// EndpointTx.sol L232-239
} else if (txType == IEndpoint.TransactionType.LinkSigner) {
    IEndpoint.LinkSigner memory txn = abi.decode(transaction[1:], ...);
    validateSender(txn.sender, sender);   // msg.sender must be owner
    requireSubaccount(txn.sender);
    linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
}
```

The slow-mode path enforces `validateSender`, which requires `msg.sender == address(uint160(bytes20(txSender)))` — i.e., the actual owner. The fast path has no equivalent owner-only guard.

---

### Impact Explanation

Once a linked signer is installed (e.g., a trading bot, API key, or third-party integration), it can:

1. Sign a `LinkSigner` transaction pointing `signer` to an attacker-controlled address.
2. The sequencer includes it in `submitTransactions()` — no on-chain permission check blocks this.
3. `linkedSigners[subaccount]` is overwritten with the attacker's address.
4. The attacker's address can now sign `WithdrawCollateral` (also `allowLinkedSigner = true`, L418-424) to drain all collateral from the subaccount.
5. The original subaccount owner retains no recourse: their nonce has advanced, and the linked signer slot is now controlled by the attacker.

The corrupted state is `linkedSigners[subaccount]` — a single storage slot that gates all privileged fast-path operations for the subaccount. The asset delta is the full collateral balance of the victim subaccount.

---

### Likelihood Explanation

**Medium-High.** The precondition is that the subaccount has a linked signer registered — a common operational pattern for traders using API keys, bots, or third-party integrations. Any such linked signer (or any party who obtains its private key) can execute this attack. The subaccount owner has no on-chain mechanism to detect or prevent the replacement before it occurs. The sequencer is the only gating layer, and it performs no ownership check on `LinkSigner` transactions beyond signature validity.

---

### Recommendation

The `LinkSigner` fast-path handler should **not** accept a linked signer's signature to modify the linked signer slot. Change `allowLinkedSigner` to `false` for this specific transaction type:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // only the subaccount owner may change the linked signer
);
```

This mirrors the slow-mode path's `validateSender` restriction and ensures that only the subaccount owner (the primary key) can mutate the linked signer slot.

---

### Proof of Concept

**Setup:** Alice owns subaccount `A`. She has registered `bot_key` as her linked signer via the slow-mode path. Attacker controls `bot_key` (e.g., a compromised API key).

**Attack:**

1. Attacker uses `bot_key` to sign a `SignedLinkSigner` transaction:
   - `sender = A`
   - `signer = attacker_address` (bytes32-encoded)
   - `nonce = current nonce of A`

2. Attacker submits this to the sequencer (or waits for it to be included via `submitTransactions()`).

3. `processTransactionImpl` reaches the `LinkSigner` branch. `validateSignedTx(..., true)` passes because `bot_key == getLinkedSigner(A)`.

4. `linkedSigners[A]` is overwritten with `attacker_address`.

5. Attacker signs a `WithdrawCollateral` transaction with `attacker_address`:
   - `sender = A`, `productId = QUOTE_PRODUCT_ID`, `amount = full balance`
   - `validateSignedTx(..., true)` passes because `attacker_address == getLinkedSigner(A)`.

6. Alice's collateral is withdrawn to the attacker.

**Key lines:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** core/contracts/EndpointTx.sol (L86-106)
```text
    function validateSignedTx(
        bytes32 sender,
        uint64 nonce,
        bytes calldata transaction,
        bytes memory signature,
        bool allowLinkedSigner
    ) internal {
        validateNonce(sender, nonce);
        validateSignature(
            sender,
            _hashTypedDataV4(
                computeDigest(
                    IEndpoint.TransactionType(uint8(transaction[0])),
                    transaction[1:]
                )
            ),
            signature,
            allowLinkedSigner
        );
        requireSubaccount(sender);
    }
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
