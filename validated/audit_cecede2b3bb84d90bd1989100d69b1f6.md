### Title
Linked Signer Can Unilaterally Replace Itself Without Subaccount Owner Authorization — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

In the fast-path transaction processor, `LinkSigner` transactions are validated with `allowLinkedSigner = true`, meaning the **current linked signer** can sign a `LinkSigner` transaction to replace itself with any arbitrary address. The subaccount owner never consents to this re-delegation. This is directly analogous to the ZecWallet finding: just as ZecWallet assumes physical device access equals authorization to send funds, Nado assumes that holding a linked signer key equals authorization to re-assign that key — a missing authentication gate on a privileged account-management operation.

---

### Finding Description

In `processTransactionImpl`, the `LinkSigner` branch validates the transaction with `allowLinkedSigner = true`:

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
        true          // ← allowLinkedSigner = true
    );
    linkedSigners[signedTx.tx.sender] = address(
        uint160(bytes20(signedTx.tx.signer))
    );
``` [1](#0-0) 

`validateSignedTx` delegates to `verifier.validateSignature`, which accepts a signature from **either** the subaccount owner address **or** the current `linkedSigner`:

```solidity
// Verifier.sol lines 297–303
address recovered = ECDSA.recover(digest, signature);
require(
    (recovered != address(0)) &&
        ((recovered == address(uint160(bytes20(sender)))) ||
            (recovered == linkedSigner)),
    ERR_INVALID_SIGNATURE
);
``` [2](#0-1) 

This means the current linked signer can sign a valid `LinkSigner` transaction and overwrite `linkedSigners[subaccount]` with any address it chooses.

**The slow-mode path for the same operation correctly restricts this to the subaccount owner only**, using `validateSender` which checks `address(uint160(bytes20(txn.sender))) == sender` (i.e., the on-chain `msg.sender` must be the owner address embedded in the subaccount):

```solidity
// EndpointTx.sol lines 232–239
} else if (txType == IEndpoint.TransactionType.LinkSigner) {
    IEndpoint.LinkSigner memory txn = abi.decode(
        transaction[1:],
        (IEndpoint.LinkSigner)
    );
    validateSender(txn.sender, sender);   // owner-only check
    requireSubaccount(txn.sender);
    linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
``` [3](#0-2) 

The inconsistency is the root cause: the slow-mode path enforces owner-only authorization for `LinkSigner`; the fast path does not.

The `linkedSigners` mapping is the sole state variable governing which address may act as a delegate signer for a subaccount: [4](#0-3) 

---

### Impact Explanation

Once the linked signer is replaced with an attacker-controlled address, that address can sign `WithdrawCollateral` and `WithdrawCollateralV2` transactions (both processed with `allowLinkedSigner = true`) to drain all collateral from the subaccount: [5](#0-4) [6](#0-5) 

**Corrupted state**: `linkedSigners[victim_subaccount]` is set to an attacker-controlled address. **Asset delta**: full collateral balance of the victim subaccount can be withdrawn to an arbitrary address via `WithdrawCollateralV2`'s `sendTo` field.

Additionally, even if the subaccount owner detects the compromise of the original linked signer key and attempts to revoke it by issuing a new `LinkSigner` transaction, the attacker has already silently rotated to a fresh key (`signerB`) that the owner is unaware of. The owner's revocation attempt targets the old key and leaves the new one active.

---

### Likelihood Explanation

Linked signers are by design hot wallets or automated trading bots — keys that are online, frequently used, and exposed to a broader attack surface than the subaccount owner's cold key. A linked signer key compromise is a realistic threat. The attacker's entry path requires no admin access, no governance capture, and no sequencer compromise: they only need to submit a sequencer-routed `LinkSigner` transaction signed by the compromised linked signer key, which is a normal protocol operation.

---

### Recommendation

Change `allowLinkedSigner` to `false` for `LinkSigner` transactions in `processTransactionImpl`. Only the subaccount owner (the address embedded in the first 20 bytes of the subaccount `bytes32`) should be authorized to change the linked signer. This aligns the fast path with the already-correct slow-mode path.

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // ← must be false: only owner may re-link
);
```

---

### Proof of Concept

1. Alice deposits collateral and links `signerA` (a hot trading bot key) to her subaccount via a `LinkSigner` transaction signed by her own key.
2. Attacker obtains `signerA`'s private key (e.g., via server compromise of the trading bot).
3. Attacker constructs a `SignedLinkSigner` transaction: `sender = alice_subaccount`, `signer = signerB` (attacker-controlled), `nonce = current_nonce`, signed by `signerA`.
4. Attacker submits this to the sequencer. The sequencer routes it through `submitTransactionsChecked` → `processTransaction` → `processTransactionImpl`.
5. `validateSignedTx` recovers `signerA` from the signature, finds it matches `getLinkedSigner(alice_subaccount)`, and passes. `linkedSigners[alice_subaccount]` is overwritten with `signerB`.
6. Attacker signs a `WithdrawCollateralV2` transaction from `signerB` with `sendTo = attacker_address`, draining Alice's full collateral balance.
7. Alice attempts to revoke `signerA` — but `signerA` is no longer the linked signer; `signerB` remains active and unknown to Alice. [1](#0-0) [7](#0-6) [8](#0-7)

### Citations

**File:** core/contracts/EndpointTx.sol (L17-23)
```text
    function validateSender(bytes32 txSender, address sender) internal view {
        require(
            address(uint160(bytes20(txSender))) == sender ||
                sender == address(this),
            ERR_SLOW_MODE_WRONG_SENDER
        );
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

**File:** core/contracts/EndpointTx.sol (L442-448)
```text
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                signedTx.tx.sendTo == address(0)
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

**File:** core/contracts/EndpointStorage.sol (L50-50)
```text
    mapping(bytes32 => address) internal linkedSigners;
```
