### Title
Linked Signer Can Unilaterally Replace Itself via Sequencer Path, Bypassing Owner-Only Invariant Enforced by Slow Mode — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

The `LinkSigner` transaction type is reachable through two distinct code paths in `EndpointTx.sol`. The slow-mode path enforces that only the actual account owner (the Ethereum address embedded in the subaccount bytes) can change the linked signer. The sequencer path does not enforce this — it accepts a signature from the **current linked signer** itself. A compromised linked signer can therefore replace itself with an attacker-controlled address, permanently taking over the subaccount without the owner's knowledge or consent.

---

### Finding Description

Two execution paths exist for the `LinkSigner` transaction type:

**Sequencer path** — `processTransactionImpl` in `EndpointTx.sol`:

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

**Slow-mode path** — `processSlowModeTransactionImpl` in `EndpointTx.sol`:

```solidity
} else if (txType == IEndpoint.TransactionType.LinkSigner) {
    IEndpoint.LinkSigner memory txn = abi.decode(
        transaction[1:],
        (IEndpoint.LinkSigner)
    );
    validateSender(txn.sender, sender);   // <-- requires msg.sender == owner address
    requireSubaccount(txn.sender);
    linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
``` [2](#0-1) 

`validateSender` enforces that the Ethereum address that submitted the slow-mode transaction matches the address embedded in the subaccount bytes — i.e., the actual owner:

```solidity
function validateSender(bytes32 txSender, address sender) internal view {
    require(
        address(uint160(bytes20(txSender))) == sender ||
            sender == address(this),
        ERR_SLOW_MODE_WRONG_SENDER
    );
}
``` [3](#0-2) 

In the sequencer path, `validateSignedTx` with `allowLinkedSigner = true` passes the current linked signer to the verifier as an acceptable signer:

```solidity
function validateSignature(..., bool allowLinkedSigner) internal virtual {
    verifier.validateSignature(
        sender,
        allowLinkedSigner ? getLinkedSigner(sender) : address(0),
        digest,
        signature
    );
}
``` [4](#0-3) 

This means the linked signer is a valid signer for a `LinkSigner` transaction that changes the linked signer. The slow-mode path closes this by requiring the actual owner's address as `msg.sender`, but the sequencer path does not.

The protocol itself acknowledges the risk of linked signers having excessive power. In `Clearinghouse.sol`, `transferQuote` explicitly blocks linked signers from transferring funds out:

```solidity
// require the sender address to be the same as the recipient address
// otherwise linked signers can transfer out
require(
    bytes20(txn.sender) == bytes20(txn.recipient),
    ERR_UNAUTHORIZED
);
``` [5](#0-4) 

No equivalent guard exists for `LinkSigner` in the sequencer path.

---

### Impact Explanation

A compromised linked signer can:
1. Sign a `LinkSigner` transaction replacing itself with an attacker-controlled address.
2. The sequencer processes it via `processTransactionImpl` with `allowLinkedSigner = true`, accepting the compromised signer's signature as valid.
3. `linkedSigners[victim_subaccount]` is now set to the attacker's address.
4. The attacker signs `WithdrawCollateral` or `WithdrawCollateralV2` transactions (both processed with `allowLinkedSigner = true`) to drain all collateral from the subaccount.

The `WithdrawCollateral` sequencer path also uses `allowLinkedSigner = true`:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    true
);
``` [6](#0-5) 

The corrupted state is `linkedSigners[subaccount]` — a permanent mapping that grants signing authority over all future sequencer-path transactions for that subaccount, including withdrawals. The account owner cannot recover without submitting a slow-mode `LinkSigner` transaction (which requires the 3-day delay).

---

### Likelihood Explanation

Linked signers are explicitly designed to be hot wallets or automated trading bots — lower-security keys exposed to online environments. Key compromise of a hot wallet is a realistic threat. The attacker needs only to obtain the linked signer's private key (e.g., via server compromise, phishing, or leaked environment variables) and submit one signed transaction to the sequencer. No admin access, governance capture, or oracle manipulation is required.

---

### Recommendation

Change `allowLinkedSigner` to `false` for the `LinkSigner` transaction type in `processTransactionImpl`. Only the account owner's key should be permitted to change the linked signer:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false  // linked signer must NOT be allowed to change itself
);
``` [7](#0-6) 

This aligns the sequencer path with the invariant already enforced by the slow-mode path, and with the protocol's own stated principle that linked signers should not be able to perform privileged account-control operations.

---

### Proof of Concept

```
1. Alice deposits collateral and registers subaccount `alice_sub`.
2. Alice submits a slow-mode LinkSigner tx setting linkedSigners[alice_sub] = hotWallet.
3. Attacker compromises hotWallet (private key leaked).
4. Attacker constructs a SignedLinkSigner struct:
       { tx: { sender: alice_sub, nonce: current_nonce, signer: attacker }, signature: hotWallet_sig }
5. Attacker submits this to the sequencer.
6. Sequencer calls processTransactionImpl → validateSignedTx(..., allowLinkedSigner=true).
7. getLinkedSigner(alice_sub) returns hotWallet; hotWallet_sig is valid → passes.
8. linkedSigners[alice_sub] = attacker.
9. Attacker constructs a SignedWithdrawCollateral signed by attacker key.
10. Sequencer processes it; validateSignedTx(..., allowLinkedSigner=true) accepts attacker as linked signer.
11. clearinghouse.withdrawCollateral drains alice_sub's balance.
```

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

**File:** core/contracts/Clearinghouse.sol (L221-225)
```text
        // otherwise linked signers can transfer out
        require(
            bytes20(txn.sender) == bytes20(txn.recipient),
            ERR_UNAUTHORIZED
        );
```
