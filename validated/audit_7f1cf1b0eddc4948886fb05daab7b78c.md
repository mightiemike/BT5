### Title
Linked Signer Can Overwrite Its Own Entry in `linkedSigners`, Enabling Permanent Subaccount Hijack — (File: `core/contracts/EndpointTx.sol`)

---

### Summary
The fast-mode `LinkSigner` transaction handler in `EndpointTx.processTransactionImpl` accepts a signature from the **currently linked signer** to overwrite the `linkedSigners` mapping for the subaccount. Because the linked signer is a session key — not the subaccount owner — this makes the `linkedSigners` mapping world-writable by any entity that was ever granted session-key access. A compromised or malicious linked signer can permanently replace itself with an attacker-controlled address, after which the attacker can sign `WithdrawCollateral`, `TransferQuote`, `MintNlp`, and `BurnNlp` transactions to drain the subaccount.

---

### Finding Description

In `EndpointTx.processTransactionImpl`, the `LinkSigner` branch calls `validateSignedTx` with `allowLinkedSigner = true`:

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
        true                          // ← allowLinkedSigner = true
    );
    linkedSigners[signedTx.tx.sender] = address(
        uint160(bytes20(signedTx.tx.signer))
    );
``` [1](#0-0) 

`validateSignedTx` with `allowLinkedSigner = true` calls `validateSignature`, which passes `getLinkedSigner(sender)` as the accepted co-signer to `verifier.validateSignature`:

```solidity
// EndpointTx.sol lines 172–184
function validateSignature(..., bool allowLinkedSigner) internal virtual {
    verifier.validateSignature(
        sender,
        allowLinkedSigner ? getLinkedSigner(sender) : address(0),
        digest,
        signature
    );
}
``` [2](#0-1) 

The verifier therefore accepts a signature from either the subaccount owner **or** the currently registered linked signer. Because the `LinkSigner` transaction itself writes to `linkedSigners[signedTx.tx.sender]`, the linked signer can sign a `LinkSigner` transaction that replaces itself with any arbitrary address — including an attacker-controlled one.

Contrast this with the slow-mode `LinkSigner` path, which correctly restricts the operation to the subaccount owner via `validateSender`:

```solidity
// EndpointTx.sol lines 232–239
} else if (txType == IEndpoint.TransactionType.LinkSigner) {
    ...
    validateSender(txn.sender, sender);   // msg.sender must be the address in subaccount bytes32
    requireSubaccount(txn.sender);
    linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
``` [3](#0-2) 

The fast-mode path has no equivalent owner-only guard, creating an asymmetry where the session key can do what only the owner should be able to do.

---

### Impact Explanation

Once the attacker's address is installed as the new linked signer, it is accepted as an authorized co-signer for every subsequent transaction type that passes `allowLinkedSigner = true`:

- `WithdrawCollateral` (line 424) — direct collateral theft
- `WithdrawCollateralV2` (line 442, conditional on `sendTo == address(0)`) — collateral theft to arbitrary address
- `TransferQuote` (line 599) — quote balance drained to any registered subaccount
- `MintNlp` / `BurnNlp` (lines 539, 559) — NLP position manipulation
- `LiquidateSubaccount` (line 397) — attacker can liquidate the victim's own subaccount [4](#0-3) [5](#0-4) 

The corrupted state is `linkedSigners[victim_subaccount]`, which is the sole trusted co-signer used to authorize all of the above operations. The asset delta is the full collateral balance of the victim subaccount.

---

### Likelihood Explanation

Linked signers are the standard mechanism for trading bots, front-end session keys, and third-party integrations. Any of the following realistic scenarios triggers the vulnerability:

1. A user grants a linked signer to a third-party trading service that later turns malicious.
2. A user's session key is leaked (e.g., stored in a browser or `.env` file).
3. A user grants a linked signer to a smart contract that has a bug allowing arbitrary calls.

In all cases, the attacker only needs to submit a single `LinkSigner` transaction to the sequencer's off-chain API — a normal, expected operation — signed with the compromised session key. No privileged access, no governance capture, and no sequencer compromise is required.

---

### Recommendation

Remove `allowLinkedSigner = true` from the fast-mode `LinkSigner` handler. The `LinkSigner` operation changes the root of signing authority for a subaccount and must only be authorized by the subaccount owner (the address embedded in the `bytes32` subaccount identifier), never by the session key it is about to replace.

```solidity
// Change:
validateSignedTx(signedTx.tx.sender, signedTx.tx.nonce, transaction, signedTx.signature, true);
// To:
validateSignedTx(signedTx.tx.sender, signedTx.tx.nonce, transaction, signedTx.signature, false);
```

This aligns the fast-mode path with the slow-mode path, which already enforces owner-only authorization via `validateSender`.

---

### Proof of Concept

1. Victim subaccount `V = bytes32(abi.encodePacked(victim_address, subaccountName))` has linked signer `S` (a session key).
2. Attacker controls `S` (compromised key, malicious service, etc.).
3. Attacker constructs:
   ```
   LinkSigner { sender: V, signer: bytes32(uint256(uint160(attacker_address))), nonce: current_nonce[victim_address] }
   ```
4. Attacker signs the EIP-712 digest of this struct with `S`'s private key.
5. Attacker submits the signed transaction to the sequencer's off-chain API (standard trading API call).
6. Sequencer includes it in a `submitTransactionsChecked` batch.
7. `EndpointTx.processTransactionImpl` validates the signature — it passes because `S == getLinkedSigner(V)`.
8. `linkedSigners[V]` is overwritten with `attacker_address`.
9. Attacker immediately submits a `WithdrawCollateral` transaction signed by `attacker_address`, draining `V`'s full collateral balance. [1](#0-0) [6](#0-5)

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

**File:** core/contracts/EndpointTx.sol (L437-465)
```text
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateralV2) {
            IEndpoint.SignedWithdrawCollateralV2 memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedWithdrawCollateralV2)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                signedTx.tx.sendTo == address(0)
            );
            int128 currentFeeX18 = spotEngine
                .getConfig(signedTx.tx.productId)
                .withdrawFeeX18;
            require(signedTx.feeX18 >= 0);
            require(signedTx.feeX18 <= currentFeeX18);
            chargeFee(
                signedTx.tx.sender,
                signedTx.feeX18,
                signedTx.tx.productId
            );
            clearinghouse.withdrawCollateral(
                signedTx.tx.sender,
                signedTx.tx.productId,
                signedTx.tx.amount,
                signedTx.tx.sendTo,
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

**File:** core/contracts/EndpointStorage.sol (L50-50)
```text
    mapping(bytes32 => address) internal linkedSigners;
```
