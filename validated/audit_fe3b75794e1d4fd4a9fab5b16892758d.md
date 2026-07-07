### Title
Linked Signer Can Unilaterally Replace Itself With an Attacker-Controlled Address - (File: `core/contracts/EndpointTx.sol`)

### Summary
In the fast-path processing of `LinkSigner` transactions, `validateSignedTx` is called with `allowLinkedSigner = true`. This permits the **current linked signer** — not just the subaccount owner — to sign and submit a `LinkSigner` transaction that replaces the linked signer with any arbitrary address. A compromised or malicious linked signer can therefore permanently hijack signing authority over a subaccount without the subaccount owner's knowledge or consent.

### Finding Description
The `EndpointTx.sol` fast-path handler for `TransactionType.LinkSigner` passes `true` as the `allowLinkedSigner` argument to `validateSignedTx`: [1](#0-0) 

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
        true          // ← allowLinkedSigner
    );
    linkedSigners[signedTx.tx.sender] = address(
        uint160(bytes20(signedTx.tx.signer))
    );
```

`validateSignedTx` internally calls `validateSignature`, which accepts a signature from **either** the subaccount owner address **or** the current `linkedSigner`: [2](#0-1) [3](#0-2) 

Because `allowLinkedSigner = true`, the existing linked signer can produce a valid signature for a `LinkSigner` transaction that writes a **new** address into `linkedSigners[subaccount]`. The subaccount owner is never consulted.

By contrast, the slow-mode path for the same transaction type correctly restricts the operation to the subaccount owner via `validateSender`: [4](#0-3) 

The asymmetry between the two paths is the root cause.

### Impact Explanation
Once a linked signer replaces itself with an attacker-controlled key, the attacker gains full signing authority over the victim subaccount. The attacker can then sign any sequencer-processed transaction on behalf of that subaccount — including `WithdrawCollateral`, `TransferQuote`, order placement, and liquidation-related operations — draining the subaccount's collateral. The `linkedSigners` mapping is the sole on-chain record of delegated authority; overwriting it is irreversible without a subsequent corrective transaction from the true owner. [5](#0-4) 

### Likelihood Explanation
Any subaccount that has ever set a linked signer is exposed. Linked signers are hot-wallet keys used for automated trading; they are a common target for key compromise. An attacker who obtains the linked signer's private key (e.g., via server breach, leaked `.env`, or supply-chain attack on a trading bot) can immediately escalate from "can trade on behalf of the account" to "can permanently redirect all future signing authority to an attacker address," with no on-chain delay or owner confirmation required.

### Recommendation
Change `allowLinkedSigner` to `false` for the `LinkSigner` fast-path handler, so that only the subaccount owner's key can authorize a change to the linked signer:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // only subaccount owner may change linked signer
);
```

This aligns the fast-path with the slow-path invariant already enforced via `validateSender`.

### Proof of Concept

1. Alice owns `alice_sub` and sets `linked_key` as her linked signer via a legitimate `LinkSigner` slow-mode transaction.
2. An attacker compromises `linked_key` (e.g., leaked private key from a trading bot).
3. The attacker constructs a `SignedLinkSigner` transaction:
   - `sender = alice_sub`
   - `signer = attacker_key` (attacker-controlled address)
   - Signs the transaction with `linked_key`.
4. The attacker submits this transaction through the sequencer's fast path.
5. `validateSignedTx(..., true)` accepts the signature because `linked_key == linkedSigners[alice_sub]`.
6. `linkedSigners[alice_sub]` is overwritten with `attacker_key`.
7. The attacker now signs `WithdrawCollateral` transactions with `attacker_key`, draining Alice's collateral. Alice's original `linked_key` is no longer valid, and Alice has no on-chain indication the change occurred until funds are gone.

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
