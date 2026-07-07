### Title
Linked Signer Can Self-Authorize `LinkSigner` Transaction to Replace Itself, Enabling Account Takeover — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

In `EndpointTx.processTransactionImpl`, the fast-mode `LinkSigner` transaction is validated with `allowLinkedSigner = true`, meaning the current linked signer can sign a `LinkSigner` transaction to replace itself with an attacker-controlled address. This is directly analogous to the `displayMessage` toggle: a caller-controlled parameter (the linked signer's key) bypasses the security control that should require the account owner's signature for account management operations. A compromised linked signer can permanently take over the subaccount's signing authority and subsequently drain all funds via `WithdrawCollateral`.

---

### Finding Description

**Root cause — `allowLinkedSigner = true` for `LinkSigner` in fast mode:**

In `processTransactionImpl`, the `LinkSigner` transaction type is validated with a hardcoded `true` for `allowLinkedSigner`: [1](#0-0) 

```solidity
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
```

`validateSignedTx` calls `validateSignature`, which passes the linked signer address to `verifier.validateSignature`: [2](#0-1) 

`verifier.validateSignature` accepts either the account owner's address **or** the linked signer's address: [3](#0-2) 

So when `allowLinkedSigner = true`, the linked signer's signature is sufficient to authorize the `LinkSigner` transaction — meaning the linked signer can set a new linked signer.

**Inconsistency with slow-mode path:**

The slow-mode `LinkSigner` handler (line 232–239) correctly enforces `validateSender(txn.sender, sender)`, which requires `msg.sender == address(uint160(bytes20(txn.sender)))` — i.e., only the account owner can set the linked signer via slow mode: [4](#0-3) 

The fast-mode path has no equivalent restriction, creating a direct inconsistency: the slow-mode path enforces owner-only authorization for `LinkSigner`, but the fast-mode path allows the linked signer to authorize it.

**Analog to `displayMessage` bug:**

In the Snap report, the dapp controls `displayMessage` to suppress showing the message to be signed — a security check that should always be enforced. In Nado, the linked signer (a delegated, less-trusted key) controls whether it can authorize account management operations by virtue of `allowLinkedSigner = true` being hardcoded for `LinkSigner`. The security check that should always be enforced (owner-only authorization for changing the linked signer) is bypassed by the caller-controlled linked signer key.

---

### Impact Explanation

A compromised or malicious linked signer can:

1. Sign a `LinkSigner` transaction setting `signer = attacker_address`
2. Submit to the sequencer API (which forwards to `processTransactionImpl`)
3. The contract accepts the linked signer's signature (`allowLinkedSigner = true`)
4. `linkedSigners[subaccount] = attacker_address`
5. Attacker uses `attacker_address` to sign `WithdrawCollateral` (also `allowLinkedSigner = true`, line 418–424) or `WithdrawCollateralV2` (when `sendTo = address(0)`, line 442–448)
6. All collateral is drained [5](#0-4) 

The corrupted state is `linkedSigners[subaccount]` — once overwritten, the account owner loses the ability to recover without submitting a slow-mode `LinkSigner` (which requires paying the slow-mode fee and waiting `SLOW_MODE_TX_DELAY`). During that window, the attacker can drain funds.

**Severity: High** — complete loss of subaccount funds for any account with a linked signer.

---

### Likelihood Explanation

Linked signers are the standard mechanism for trading bots, API keys, and automated strategies in perpetual DEX protocols. These are hot keys stored on servers, making them significantly higher-risk targets than cold-storage account owner keys. A server compromise, leaked `.env` file, or malicious trading bot integration is a realistic attack vector. The attacker only needs the linked signer's private key — no privileged protocol access is required.

---

### Recommendation

Change `allowLinkedSigner` to `false` for `LinkSigner` transactions in `processTransactionImpl`, consistent with the slow-mode path. Only the account owner should be able to authorize changes to the linked signer:

```solidity
// Before (vulnerable):
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    true   // ← allows linked signer to change linked signer
);

// After (fixed):
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false  // ← only account owner can change linked signer
);
```

This aligns the fast-mode path with the slow-mode path's security invariant.

---

### Proof of Concept

1. User `Alice` has subaccount `A` with linked signer `L` (a trading bot key stored on a server).
2. Attacker compromises `L`'s private key (e.g., via server breach).
3. Attacker constructs `SignedLinkSigner { tx: { sender: A, signer: attacker_key, nonce: N }, signature: sig_L }` where `sig_L` is signed by `L`.
4. Attacker submits to the sequencer API.
5. Sequencer calls `processTransactionImpl` with this transaction.
6. `validateSignedTx(..., true)` → `validateSignature(A, digest, sig_L, true)` → `verifier.validateSignature(A, L, digest, sig_L)` → `ECDSA.recover(digest, sig_L) == L` → **accepted**.
7. `linkedSigners[A] = attacker_key`.
8. Attacker signs `SignedWithdrawCollateral { tx: { sender: A, productId: QUOTE, amount: MAX, nonce: N+1 }, signature: sig_attacker }`.
9. `validateSignedTx(..., true)` accepts `sig_attacker` because `linkedSigners[A] == attacker_key`.
10. `clearinghouse.withdrawCollateral(A, QUOTE, MAX, address(0), ...)` — all funds drained. [6](#0-5) [1](#0-0) [3](#0-2)

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
