### Title
Linked Signer Can Self-Escalate to Override Subaccount Owner's Signer Assignment — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

In `EndpointTx.processTransactionImpl`, the `LinkSigner` transaction type is validated with `allowLinkedSigner = true`. This means the currently active linked signer — a delegated role set by the subaccount owner — can unilaterally sign a new `LinkSigner` transaction to replace itself with any arbitrary address, including an attacker-controlled one. The subaccount owner's original delegation choice is silently overridden without their consent or signature.

---

### Finding Description

In `processTransactionImpl`, the `LinkSigner` branch is:

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
        true   // <-- allowLinkedSigner = true
    );
    linkedSigners[signedTx.tx.sender] = address(
        uint160(bytes20(signedTx.tx.signer))
    );
``` [1](#0-0) 

`validateSignedTx` with `allowLinkedSigner = true` calls `validateSignature`, which passes `getLinkedSigner(sender)` as the accepted signer to `Verifier.validateSignature`:

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

`Verifier.validateSignature` accepts the signature if it recovers to either the subaccount owner address or the linked signer:

```solidity
require(
    (recovered != address(0)) &&
        ((recovered == address(uint160(bytes20(sender)))) ||
            (recovered == linkedSigner)),
    ERR_INVALID_SIGNATURE
);
``` [3](#0-2) 

The result: the currently linked signer can sign a `LinkSigner` transaction to write any new address into `linkedSigners[subaccount]`, overriding the subaccount owner's original delegation without requiring the owner's key.

---

### Impact Explanation

Once the linked signer replaces itself with an attacker-controlled address, the attacker's address becomes the new linked signer. The attacker can then sign `TransferQuote` transactions (also validated with `allowLinkedSigner = true`) to transfer quote-token balances out of the victim's subaccount to any recipient subaccount:

```solidity
} else if (txType == IEndpoint.TransactionType.TransferQuote) {
    ...
    validateSignedTx(
        signedTx.tx.sender,
        signedTx.tx.nonce,
        transaction,
        signedTx.signature,
        true   // allowLinkedSigner = true
    );
    ...
    clearinghouse.transferQuote(signedTx.tx);
``` [4](#0-3) 

This constitutes direct, irreversible loss of user funds. The corrupted state is `linkedSigners[victim_subaccount]` and the resulting `clearinghouse` balance delta for the victim.

---

### Likelihood Explanation

The linked signer is typically a hot wallet or automated trading key — a common target for compromise. Any party who obtains the linked signer's private key (e.g., via a compromised API key, leaked `.env`, or malicious dependency) can execute this attack in a single sequencer submission. No special protocol access is required beyond knowledge of the victim's current nonce (publicly readable via `getNonce`). [5](#0-4) 

---

### Recommendation

Change `allowLinkedSigner` to `false` for the `LinkSigner` transaction type in `processTransactionImpl`. Only the subaccount owner (the address embedded in the `sender` bytes32) should be permitted to change the linked signer. The linked signer is a delegated role and must not be able to re-delegate that role to a third party. [1](#0-0) 

---

### Proof of Concept

1. Alice owns subaccount `alice_sub` and sets linked signer to `bob_addr` via a valid `LinkSigner` transaction signed by Alice's key.
2. `bob_addr` is compromised. The attacker calls `endpoint.getNonce(alice_address)` to read the current nonce `N`.
3. The attacker constructs a `SignedLinkSigner` payload: `{ sender: alice_sub, signer: mallory_sub, nonce: N }`, signed by `bob_addr`.
4. The attacker submits this to the sequencer via `submitTransactionsChecked`. The sequencer processes it; `validateSignedTx` accepts `bob_addr`'s signature because `allowLinkedSigner = true` and `bob_addr == linkedSigners[alice_sub]`.
5. `linkedSigners[alice_sub]` is now set to `mallory_addr`.
6. The attacker constructs a `SignedTransferQuote` payload: `{ sender: alice_sub, recipient: mallory_sub, amount: full_balance, nonce: N+1 }`, signed by `mallory_addr`.
7. The sequencer processes it; `mallory_addr` is accepted as the linked signer. `clearinghouse.transferQuote` moves Alice's entire quote balance to `mallory_sub`.
8. Alice's funds are drained with no on-chain action required from Alice. [6](#0-5) [7](#0-6)

### Citations

**File:** core/contracts/EndpointTx.sol (L72-77)
```text
    function validateNonce(bytes32 sender, uint64 nonce) internal virtual {
        require(
            nonce == nonces[address(uint160(bytes20(sender)))]++,
            ERR_WRONG_NONCE
        );
    }
```

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

**File:** core/contracts/Endpoint.sol (L401-403)
```text
    function getNonce(address sender) external view returns (uint64) {
        return nonces[sender];
    }
```
