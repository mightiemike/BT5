### Title
Compromised `linkedSigner` Can Perpetually Re-Authorize Itself via `LinkSigner` Fast-Path — (`File: core/contracts/EndpointTx.sol`)

---

### Summary

In `EndpointTx.processTransactionImpl`, the fast-path `LinkSigner` transaction is validated with `allowLinkedSigner = true`. This means the **current linked signer** — not just the subaccount owner — can sign a `LinkSigner` transaction to change the linked signer to any address, including themselves. A compromised linked signer can therefore perpetually re-link themselves (or a new attacker-controlled address) after every revocation attempt by the account owner, maintaining persistent unauthorized access to the subaccount.

---

### Finding Description

The `LinkSigner` transaction type is processed in two paths:

**Slow-mode path** (`processSlowModeTransactionImpl`): Validates only that `msg.sender` matches the subaccount address via `validateSender`. No linked signer involvement. [1](#0-0) 

**Fast-path** (`processTransactionImpl`): Calls `validateSignedTx` with `allowLinkedSigner = true`: [2](#0-1) 

The `allowLinkedSigner = true` flag causes `validateSignature` to accept a signature from either the subaccount owner **or** the current `linkedSigners[sender]`: [3](#0-2) 

Which delegates to `Verifier.validateSignature`: [4](#0-3) 

This means the current linked signer can sign a `LinkSigner` transaction to overwrite `linkedSigners[subaccount]` with any address — including themselves.

The `linkedSigners` mapping is a simple last-write-wins store with no epoch history: [5](#0-4) 

---

### Impact Explanation

A compromised linked signer has access to all high-privilege fast-path operations: `WithdrawCollateralV2` (fund drain), `LiquidateSubaccount`, `MintNlp`/`BurnNlp`, and `TransferQuote`. The linked signer can also sign a new `LinkSigner` transaction to re-establish their own access after every revocation, creating a persistent attack loop. The account owner cannot effectively revoke a compromised linked signer through the slow-mode path because the attacker can always re-link themselves via the fast path before or after the slow-mode revocation is processed.

---

### Likelihood Explanation

Any user who has ever set a linked signer (session key) is exposed if that key is compromised. The attack requires no privileged access beyond possession of the linked signer's private key. The attacker simply submits a signed `LinkSigner` transaction to the sequencer (normal off-chain operation) with the next valid nonce. The slow-mode revocation has a hardcoded 3-day delay: [6](#0-5) 

During and after that delay, the attacker can re-link themselves indefinitely. Linked signers are a core protocol feature (session keys for trading bots, UI sessions), making this a realistic and high-likelihood scenario.

---

### Recommendation

Change `allowLinkedSigner` to `false` for the fast-path `LinkSigner` transaction in `processTransactionImpl`. Only the subaccount owner (the address embedded in `sender`) should be permitted to authorize a linked signer change:

```solidity
// In processTransactionImpl, LinkSigner branch:
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false  // ← was true; only owner may change linked signer
);
``` [7](#0-6) 

This mirrors the slow-mode path, which already enforces owner-only authorization via `validateSender`.

---

### Proof of Concept

**Setup:**
- Subaccount `alice` has `linkedSigners[alice] = attackerEOA`
- `nonces[alice] = 5`

**Step 1 — Alice attempts revocation via slow mode:**
Alice calls `submitSlowModeTransaction` with a `LinkSigner` payload setting `signer = address(0)`. This is queued with `executableAt = block.timestamp + 3 days`. [8](#0-7) 

**Step 2 — Attacker re-links before revocation executes:**
The attacker signs a fast-path `LinkSigner` transaction:
```
sender = alice
signer = attackerEOA   // re-link self
nonce  = 5
signature = sign(digest, attackerEOA_privkey)
```
The attacker submits this to the sequencer (normal off-chain submission). The sequencer includes it in the next batch via `submitTransactionsChecked`. [9](#0-8) 

`validateSignedTx` accepts the signature because `getLinkedSigner(alice) == attackerEOA` and `allowLinkedSigner = true`. `linkedSigners[alice]` is set back to `attackerEOA`. Nonce advances to 6.

**Step 3 — Alice's slow-mode revocation executes after 3 days:**
`linkedSigners[alice]` is set to `address(0)`.

**Step 4 — Attacker re-links again:**
Attacker signs a new `LinkSigner` with `nonce = 6`. But `linkedSigners[alice]` is now `address(0)`, so `getLinkedSigner(alice) == address(0)`. The signature check in `validateSignature` requires `recovered == address(uint160(bytes20(alice)))` or `recovered == address(0)`. Since `address(0)` is explicitly excluded by the `recovered != address(0)` guard, this step would fail. [10](#0-9) 

**Revised Step 4 — Attacker acts before slow-mode executes:**
The attacker does not wait for the slow-mode revocation. Instead, they drain the account (via `WithdrawCollateralV2`) and re-link themselves repeatedly during the 3-day window. Each re-link uses the next nonce and is accepted because `linkedSigners[alice]` still points to `attackerEOA` until the slow-mode transaction is processed. The attacker can also front-run the slow-mode execution by submitting a new `LinkSigner` immediately after each slow-mode revocation, as long as the sequencer includes their transaction before the next slow-mode execution.

The core broken invariant: **the entity being revoked can authorize its own re-authorization**, directly analogous to the Axelar finding where old operator sets could sign new commands after operatorship transfer.

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

**File:** core/contracts/EndpointTx.sol (L374-380)
```text
        IEndpoint.SlowModeConfig memory _slowModeConfig = slowModeConfig;
        requireUnsanctioned(sender);
        slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: transaction
        });
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

**File:** core/contracts/EndpointStorage.sol (L50-50)
```text
    mapping(bytes32 => address) internal linkedSigners;
```

**File:** core/contracts/Endpoint.sol (L271-294)
```text
    function submitTransactionsChecked(
        uint64 idx,
        bytes[] calldata transactions,
        bytes32 e,
        bytes32 s,
        uint8 signerBitmask
    ) external {
        validateSubmissionIdx(idx);
        require(msg.sender == sequencer);
        // TODO: if one of these transactions fails this means the sequencer is in an error state
        // we should probably record this, and engage some sort of recovery mode

        bytes32 digest = keccak256(abi.encode(idx));
        for (uint256 i = 0; i < transactions.length; ++i) {
            digest = keccak256(abi.encodePacked(digest, transactions[i]));
        }
        verifier.requireValidSignature(digest, e, s, signerBitmask);

        for (uint256 i = 0; i < transactions.length; i++) {
            bytes calldata transaction = transactions[i];
            processTransaction(transaction);
            nSubmissions += 1;
        }
    }
```
