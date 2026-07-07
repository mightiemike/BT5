### Title
Linked Signer Can Perpetuate Its Own Access and Drain Funds During Mandatory 3-Day Revocation Delay — (`File: core/contracts/EndpointTx.sol`)

---

### Summary

The fast-mode `LinkSigner` transaction in `EndpointTx.sol` is validated with `allowLinkedSigner = true`, meaning the **current linked signer** can sign a new `LinkSigner` transaction to replace itself with any address it controls. When a user attempts to revoke a malicious linked signer, their only reliable recourse is the slow-mode path, which enforces a hardcoded 3-day delay. During that window the malicious linked signer retains full signing authority and can drain the subaccount via `TransferQuote`.

---

### Finding Description

`EndpointTx.processTransactionImpl` handles the fast-mode `LinkSigner` transaction at lines 576–590:

```solidity
} else if (txType == IEndpoint.TransactionType.LinkSigner) {
    IEndpoint.SignedLinkSigner memory signedTx = abi.decode(...);
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
}
``` [1](#0-0) 

`validateSignedTx` with `allowLinkedSigner = true` calls `validateSignature`, which passes `getLinkedSigner(sender)` to `Verifier.validateSignature`. The verifier accepts the signature if it was produced by **either** the subaccount owner address **or** the current linked signer:

```solidity
require(
    (recovered != address(0)) &&
        ((recovered == address(uint160(bytes20(sender)))) ||
            (recovered == linkedSigner)),
    ERR_INVALID_SIGNATURE
);
``` [2](#0-1) 

This means the linked signer can unilaterally sign a `LinkSigner` transaction that replaces itself with a new address it controls, consuming the current nonce and invalidating any concurrent revocation attempt by the real owner in fast mode.

The slow-mode `LinkSigner` path (lines 232–239) correctly uses `validateSender` (owner-only), but it enqueues the transaction with a hardcoded 3-day delay:

```solidity
slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
    executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY,
    ...
});
``` [3](#0-2) 

During those 3 days the linked signer retains full authority. `TransferQuote` is also validated with `allowLinkedSigner = true`:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    true          // ← allowLinkedSigner
);
...
clearinghouse.transferQuote(signedTx.tx);
``` [4](#0-3) 

The recipient of `TransferQuote` is any registered subaccount (`_recordSubaccount` merely registers it if new). A malicious linked signer can therefore transfer the victim's entire quote balance to a subaccount they own, then withdraw it.

---

### Impact Explanation

A malicious or compromised linked signer can:

1. **Perpetuate its own access** — sign a new `LinkSigner` to rotate to a fresh address it controls, consuming the nonce and blocking the owner's fast-mode revocation attempt.
2. **Drain the subaccount** — during the mandatory 3-day slow-mode delay, sign `TransferQuote` transactions to move all quote collateral to an attacker-controlled subaccount, then withdraw.

The corrupted state is `linkedSigners[subaccount]` (signer state) and the subaccount's quote balance (asset delta). The user suffers complete loss of funds with no on-chain mechanism to stop it within the delay window.

---

### Likelihood Explanation

Any user who has ever called `LinkSigner` (e.g., to authorize an API trading key) is exposed if that key is later compromised or turns malicious. API key compromise is a realistic and common event in DeFi trading infrastructure. The attack requires no privileged access beyond the already-linked signer key.

---

### Recommendation

Change `allowLinkedSigner` to `false` for the fast-mode `LinkSigner` handler so that only the subaccount owner's key can authorize a linked-signer change:

```solidity
// EndpointTx.sol ~L581
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // only the subaccount owner may change the linked signer
);
``` [5](#0-4) 

This mirrors the slow-mode path, which already enforces owner-only authorization via `validateSender`.

---

### Proof of Concept

1. Alice owns subaccount `A` and calls `LinkSigner` to authorize trading key `B` (nonce = 0 → 1).
2. Key `B` is compromised. Alice submits a slow-mode `LinkSigner(sender=A, signer=0x0)` to revoke it. The transaction is queued with `executableAt = now + 3 days`.
3. During the 3-day window, key `B` signs a fast-mode `TransferQuote(sender=A, recipient=C, amount=ALL)` where `C` is an attacker-controlled subaccount. The sequencer processes it; Alice's entire quote balance moves to `C`.
4. Key `B` (or the attacker) withdraws from subaccount `C`.
5. After 3 days Alice's revocation executes — but the funds are already gone.

Additionally, if Alice attempts a fast-mode revocation (nonce = 1), key `B` can front-run by submitting `LinkSigner(sender=A, signer=B')` with nonce = 1 first, rotating to a fresh key `B'` it controls and invalidating Alice's transaction with `ERR_WRONG_NONCE`. [6](#0-5) [7](#0-6)

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

**File:** core/contracts/EndpointTx.sol (L376-380)
```text
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

**File:** core/contracts/EndpointTx.sol (L599-614)
```text
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

**File:** core/contracts/Verifier.sol (L298-303)
```text
        require(
            (recovered != address(0)) &&
                ((recovered == address(uint160(bytes20(sender)))) ||
                    (recovered == linkedSigner)),
            ERR_INVALID_SIGNATURE
        );
```

**File:** core/contracts/EndpointStorage.sol (L34-34)
```text
    mapping(address => uint64) internal nonces;
```
