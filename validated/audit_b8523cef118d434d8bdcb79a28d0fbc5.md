### Title
Linked Signer Can Unilaterally Rebind Itself to Strand Isolated Subaccount Management — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

The fast-mode `LinkSigner` transaction handler in `EndpointTx.sol` accepts signatures from the **current linked signer** (`allowLinkedSigner = true`). This allows a malicious or compromised linked signer to unilaterally overwrite the linked signer pointer — including zeroing it out — immediately stranding all isolated subaccounts that depend on the old linked signer for management authorization, because `getLinkedSigner` for isolated subaccounts dynamically reads from the parent's live `linkedSigners` mapping.

---

### Finding Description

In `EndpointTx.sol`, the fast-mode `LinkSigner` handler calls `validateSignedTx` with `allowLinkedSigner = true`: [1](#0-0) 

`validateSignedTx` with `allowLinkedSigner = true` routes through `validateCompactSignature`, which passes `getLinkedSigner(sender)` to the verifier as an accepted signer: [2](#0-1) 

This means the **current linked signer** can produce a valid signature for a `LinkSigner` transaction, allowing it to overwrite `linkedSigners[subaccount]` to any address — including `address(0)`.

The critical desynchronization arises because `getLinkedSigner` for isolated subaccounts does **not** store the signer at creation time; it dynamically reads from the parent's current `linkedSigners` entry at every call: [3](#0-2) 

The isolated subaccount → parent mapping is stored in `OffchainExchange.sol`: [4](#0-3) 

When the linked signer pointer is changed, **all** isolated subaccounts derived from the parent immediately reflect the new (or zero) signer. There is no settlement, no grace period, and no preservation of the old signer's in-flight management rights.

By contrast, the slow-mode `LinkSigner` path uses `validateSender`, which requires the actual subaccount owner's Ethereum address — the linked signer cannot exploit that path: [5](#0-4) 

---

### Impact Explanation

A malicious or compromised linked signer can zero out (or redirect) the linked signer pointer for a parent subaccount. The moment the sequencer processes the transaction:

- `getLinkedSigner` returns `address(0)` (or the new address) for every isolated subaccount of that parent.
- The old linked signer immediately loses authorization to submit any transaction on behalf of those isolated subaccounts.
- Open positions in isolated subaccounts that were under active management (e.g., automated stop-loss, delta-hedging, margin top-ups) can no longer be acted on by the old signer.
- If the user is relying on the linked signer for automated risk management, those positions can drift into liquidation range before the subaccount owner can react and issue a new `LinkSigner` via their own key.

The subaccount owner retains their own key and can recover by re-linking, but the stranding is immediate and irreversible until the owner acts — and the owner may not be monitoring in real time.

---

### Likelihood Explanation

- The linked signer is explicitly listed as a valid attacker profile in the audit objective.
- The attack requires no privileged keys, no oracle manipulation, and no governance capture.
- The subaccount's current nonce is public on-chain; the linked signer can read it and construct a valid `LinkSigner` transaction trivially.
- Any linked signer that turns adversarial (compromised API key, malicious trading bot, rogue counterparty) can execute this in a single sequencer submission.

---

### Recommendation

Set `allowLinkedSigner = false` for the fast-mode `LinkSigner` transaction type. The linked signer should be able to act *under* the delegation, not *rewrite* it. Only the subaccount owner (whose address is embedded in the subaccount bytes32) should be permitted to change the linked signer pointer. This mirrors the slow-mode path, which already enforces `validateSender` and does not accept linked signer signatures.

---

### Proof of Concept

1. User A has subaccount `SA` with linked signer B (e.g., a trading bot API key).
2. User A creates isolated subaccounts `ISO1`, `ISO2` via `CreateIsolatedSubaccount`; both inherit B as their linked signer through `getLinkedSigner`.
3. B reads A's current nonce from the chain (public storage).
4. B constructs a fast-mode `LinkSigner` transaction: `sender = SA`, `signer = address(0)`, `nonce = current_nonce`.
5. B signs it with its own key — `validateCompactSignature` accepts it because `allowLinkedSigner = true` and B is the current linked signer.
6. B submits the transaction to the sequencer.
7. The sequencer processes it: `linkedSigners[SA] = address(0)`.
8. `getLinkedSigner(ISO1)` and `getLinkedSigner(ISO2)` now return `address(0)`.
9. B can no longer submit any transaction for `ISO1` or `ISO2`.
10. Any open perp positions in `ISO1`/`ISO2` that B was managing (stop-loss, margin maintenance) are stranded; the positions remain open and unmanaged until A manually intervenes with their own key.

### Citations

**File:** core/contracts/EndpointTx.sol (L143-157)
```text
    function getLinkedSigner(bytes32 subaccount)
        public
        view
        virtual
        returns (address)
    {
        return
            RiskHelper.isIsolatedSubaccount(subaccount)
                ? linkedSigners[
                    IOffchainExchange(offchainExchange).getParentSubaccount(
                        subaccount
                    )
                ]
                : linkedSigners[subaccount];
    }
```

**File:** core/contracts/EndpointTx.sol (L186-198)
```text
    function validateCompactSignature(
        bytes32 sender,
        bytes32 digest,
        IEndpoint.CompactSignature memory signature,
        bool allowLinkedSigner
    ) internal virtual {
        verifier.validateCompactSignature(
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

**File:** core/contracts/OffchainExchange.sol (L49-53)
```text
    mapping(bytes32 => bytes32) internal parentSubaccounts;

    // (subaccount, id) -> isolated subaccount
    mapping(bytes32 => mapping(uint256 => bytes32))
        internal isolatedSubaccounts;
```
