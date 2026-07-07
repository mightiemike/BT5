### Title
Linked Signer Can Hijack Subaccount by Re-Linking to Attacker-Controlled Address — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

The `LinkSigner` transaction in `processTransactionImpl` is validated with `allowLinkedSigner = true`, meaning the **current linked signer** can authorize a new linked signer for the subaccount. This creates a trust-chain weakness structurally identical to the SignalService report: an intermediate trusted node (the linked signer, analogous to an intermediate hop) can re-delegate authority to a malicious address, permanently hijacking the subaccount without the original owner's consent.

---

### Finding Description

In `EndpointTx.sol`, `processTransactionImpl` handles `LinkSigner` at lines 576–590:

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
        true          // <-- allowLinkedSigner
    );
    linkedSigners[signedTx.tx.sender] = address(
        uint160(bytes20(signedTx.tx.signer))
    );
}
``` [1](#0-0) 

`validateSignedTx` with `allowLinkedSigner = true` calls `validateSignature`, which passes `getLinkedSigner(sender)` as the accepted signer:

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

`verifier.validateSignature` accepts a signature from either the subaccount owner address OR the linked signer:

```solidity
require(
    (recovered != address(0)) &&
        ((recovered == address(uint160(bytes20(sender)))) ||
            (recovered == linkedSigner)),
    ERR_INVALID_SIGNATURE
);
``` [3](#0-2) 

Therefore, the **current linked signer** can sign and submit a `LinkSigner` transaction that replaces the linked signer with any arbitrary address.

**Contrast with the slow-mode path**, where `LinkSigner` uses `validateSender`, which enforces that only the Ethereum address embedded in the subaccount bytes32 (the true owner) can set the linked signer:

```solidity
} else if (txType == IEndpoint.TransactionType.LinkSigner) {
    ...
    validateSender(txn.sender, sender);   // msg.sender must be the owner address
    linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
}
``` [4](#0-3) 

The slow-mode path correctly restricts `LinkSigner` to the owner. The fast (sequencer-batched) path does not. This asymmetry is the root cause.

The corrupted state is `linkedSigners[subaccount]` in `EndpointStorage.sol`: [5](#0-4) 

---

### Impact Explanation

Once the linked signer re-links the subaccount to an attacker-controlled address, the attacker has full signing authority. They can:

- Submit `WithdrawCollateral` / `WithdrawCollateralV2` transactions to drain all collateral from the subaccount.
- Submit `TransferQuote` transactions to move quote balances to attacker-controlled subaccounts.
- Place adversarial trades via `MatchOrders` that extract value.

The corrupted asset delta is the entire collateral balance of the victim subaccount across all spot products. The original owner has no on-chain mechanism to detect the re-linking until funds are already drained, because `linkedSigners` is not emitted as an event in the fast path.

---

### Likelihood Explanation

Linked signers are a primary feature of the protocol, used for API trading and automated strategies. Any user who has set a linked signer (a hot wallet or API key with weaker security guarantees than the main wallet) is exposed. The attack requires only that the linked signer key be compromised or malicious — no sequencer compromise, no governance capture, no admin keys. The sequencer acts as a neutral relay and has no basis to reject a structurally valid signed transaction. The nonce is public state (`nonces[address]`), so the attacker can construct a valid transaction without any additional information.

---

### Recommendation

`LinkSigner` transactions in `processTransactionImpl` should be validated with `allowLinkedSigner = false`, restricting authorization to the subaccount owner only — consistent with the slow-mode path behavior:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // only owner can re-link
);
``` [6](#0-5) 

---

### Proof of Concept

1. Alice owns subaccount `alice_sub` and sets linked signer to address `B` (her API key) via a legitimate `LinkSigner` transaction.
2. Attacker compromises or controls `B`.
3. Attacker constructs a `SignedLinkSigner` transaction:
   - `sender = alice_sub`
   - `signer = attacker_address` (bytes32-encoded)
   - `nonce = nonces[address(alice)]` (read from public state)
   - Signed by `B`
4. Attacker submits this transaction to the sequencer's off-chain API. The sequencer, acting as a neutral relay, includes it in the next `submitTransactionsChecked` batch.
5. On-chain: `validateSignedTx(..., true)` recovers `B` from the signature, checks `B == getLinkedSigner(alice_sub)` — passes.
6. `linkedSigners[alice_sub] = attacker_address`.
7. Attacker submits `WithdrawCollateral` signed by `attacker_address` — drains all of Alice's collateral.

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
