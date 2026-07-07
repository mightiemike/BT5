### Title
Linked Signer Can Self-Escalate by Overwriting `linkedSigners` Mapping — (`File: core/contracts/EndpointTx.sol`)

---

### Summary

The `LinkSigner` sequencer-path handler in `EndpointTx.sol` calls `validateSignedTx` with `allowLinkedSigner = true`. This means the **current linked signer** — a delegated hot-wallet key — can sign a `LinkSigner` transaction that replaces itself with any attacker-controlled address. The `linkedSigners` mapping is the on-chain "credential store" that governs who may authorize all high-value subaccount operations. Allowing the linked signer to mutate that store is the direct analog to the clipboard credential-exposure class: a delegated, lower-trust principal can overwrite the authorization credential with an attacker-controlled value, granting the attacker persistent signing authority over the subaccount.

---

### Finding Description

In the sequencer transaction pipeline, `processTransactionImpl` handles `TransactionType.LinkSigner` as follows:

```solidity
// EndpointTx.sol lines 576-590
} else if (txType == IEndpoint.TransactionType.LinkSigner) {
    IEndpoint.SignedLinkSigner memory signedTx = abi.decode(
        transaction[1:], (IEndpoint.SignedLinkSigner)
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
``` [1](#0-0) 

`validateSignedTx` with `allowLinkedSigner = true` resolves the permitted signer set to `{subaccount_owner, current_linked_signer}`:

```solidity
// EndpointTx.sol lines 172-184
function validateSignature(..., bool allowLinkedSigner) internal virtual {
    verifier.validateSignature(
        sender,
        allowLinkedSigner ? getLinkedSigner(sender) : address(0),
        digest,
        signature
    );
}
``` [2](#0-1) 

`Verifier.validateSignature` accepts the signature if the recovered address equals either the subaccount owner **or** the linked signer:

```solidity
// Verifier.sol lines 297-303
address recovered = ECDSA.recover(digest, signature);
require(
    (recovered != address(0)) &&
    ((recovered == address(uint160(bytes20(sender)))) ||
     (recovered == linkedSigner)),
    ERR_INVALID_SIGNATURE
);
``` [3](#0-2) 

Therefore, the current linked signer can produce a valid `SignedLinkSigner` transaction that sets `linkedSigners[subaccount]` to any address — including an attacker-controlled one — without the subaccount owner's involvement.

Compare this to the **slow-mode** `LinkSigner` path, which enforces `validateSender(txn.sender, sender)` — requiring `msg.sender` to be the subaccount owner's EOA:

```solidity
// EndpointTx.sol lines 232-239
} else if (txType == IEndpoint.TransactionType.LinkSigner) {
    ...
    validateSender(txn.sender, sender);   // ← owner-only in slow mode
    requireSubaccount(txn.sender);
    linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
``` [4](#0-3) 

The slow-mode path correctly restricts `LinkSigner` to the owner. The sequencer path does not.

---

### Impact Explanation

Once the attacker's address is installed as the linked signer, it can authorize every sequencer-path transaction that passes `allowLinkedSigner = true`, including:

- **`TransferQuote`** (lines 599–614): transfers quote balance to any registered subaccount the attacker controls, from which a normal withdrawal can be executed.
- **`WithdrawCollateral` V1** (lines 418–436): withdraws to `address(0)`, which `Clearinghouse.withdrawCollateral` resolves to the subaccount owner's address — not directly useful for theft, but confirms the linked signer has full withdrawal signing power.
- **`WithdrawCollateralV2`** (lines 442–465): the linked signer is permitted when `sendTo == address(0)`, again resolving to the owner's address. [5](#0-4) [6](#0-5) 

The concrete theft path is via `TransferQuote`: attacker-as-linked-signer signs a `TransferQuote` moving the victim's quote balance to an attacker-owned subaccount, then withdraws normally. Additionally, the attacker gains **persistence**: even if the victim later rotates their linked signer key, the attacker can race to re-install their address before the rotation is processed, since the sequencer controls ordering.

**Impact: 5** — direct, complete loss of subaccount funds for any user whose linked signer key is compromised.

---

### Likelihood Explanation

Linked signers are the standard API-trading credential in perpetuals DEXes. They are hot-wallet keys stored in servers, bots, and browser extensions — all high-exposure environments. A key leak (server breach, phishing, clipboard exposure of the private key, malicious npm package) is a realistic and common threat. The on-chain exploit requires only that the attacker submit one `LinkSigner` transaction through the sequencer, which is a normal, unprivileged operation.

**Likelihood: 4** — linked signer key compromise is a well-documented real-world attack vector; the on-chain step is trivial once the key is obtained.

---

### Recommendation

Restrict the `LinkSigner` sequencer-path handler to accept only a signature from the **subaccount owner**, not the current linked signer. Change `allowLinkedSigner` to `false` for this transaction type:

```solidity
// EndpointTx.sol — LinkSigner sequencer path
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // ← owner-only; linked signer must not self-escalate
);
```

This aligns the sequencer path with the slow-mode path, which already enforces owner-only authorization for `LinkSigner`. The linked signer retains all trading capabilities; it simply cannot modify the authorization credential that governs its own existence.

---

### Proof of Concept

1. Victim subaccount `0xAlice000...` has linked signer `0xHotKey` registered via a prior `LinkSigner` transaction.
2. Attacker obtains `0xHotKey`'s private key (clipboard leak, server breach, etc.).
3. Attacker constructs a `SignedLinkSigner` transaction:
   - `sender = 0xAlice000...`
   - `signer = bytes32(uint256(uint160(0xAttacker)))`
   - `nonce = current nonce for 0xAlice`
   - Signs with `0xHotKey` (valid because `allowLinkedSigner = true`)
4. Attacker submits to the sequencer. `validateSignedTx` passes; `linkedSigners[0xAlice000...] = 0xAttacker`.
5. Attacker constructs a `SignedTransferQuote` transaction:
   - `sender = 0xAlice000...`, `recipient = 0xAttackerSubaccount`, `amount = full balance`
   - Signs with `0xAttacker` (now the registered linked signer)
6. Sequencer processes; `clearinghouse.transferQuote` moves Alice's entire quote balance to the attacker's subaccount.
7. Attacker withdraws from `0xAttackerSubaccount` via a normal `WithdrawCollateral` signed by `0xAttacker` directly. [1](#0-0) [7](#0-6) [8](#0-7)

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

**File:** core/contracts/EndpointTx.sol (L593-614)
```text
        } else if (txType == IEndpoint.TransactionType.TransferQuote) {
            IEndpoint.SignedTransferQuote memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedTransferQuote)
            );
            _recordSubaccount(signedTx.tx.recipient);
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            if (
                RiskHelper.isIsolatedSubaccount(signedTx.tx.recipient) ||
                RiskHelper.isIsolatedSubaccount(signedTx.tx.sender)
            ) {
                chargeFee(signedTx.tx.sender, HEALTHCHECK_FEE / 10);
            } else {
                chargeFee(signedTx.tx.sender, HEALTHCHECK_FEE);
            }
            clearinghouse.transferQuote(signedTx.tx);
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

**File:** core/contracts/Clearinghouse.sol (L404-406)
```text
        if (sendTo == address(0)) {
            sendTo = address(uint160(bytes20(sender)));
        }
```

**File:** core/contracts/EndpointStorage.sol (L50-51)
```text
    mapping(bytes32 => address) internal linkedSigners;

```
