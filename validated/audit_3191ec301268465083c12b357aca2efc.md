### Title
Linked Signer Can Irrevocably Escalate Its Own Delegation by Self-Issuing a `LinkSigner` Transaction - (File: `core/contracts/EndpointTx.sol`)

---

### Summary

The `LinkSigner` transaction handler in `EndpointTx.processTransactionImpl` passes `allowLinkedSigner = true` to `validateSignedTx`. This means the **current linked signer** — not just the subaccount owner — can sign a `LinkSigner` transaction to replace itself with any arbitrary new address. A compromised or malicious linked signer can therefore permanently persist its delegation by continuously re-linking itself or a colluding address, defeating any owner-initiated revocation attempt.

---

### Finding Description

In `EndpointTx.processTransactionImpl`, the `LinkSigner` branch is:

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
        true          // <-- allowLinkedSigner = true
    );
    linkedSigners[signedTx.tx.sender] = address(
        uint160(bytes20(signedTx.tx.signer))
    );
``` [1](#0-0) 

`validateSignedTx` with `allowLinkedSigner = true` calls `validateSignature`, which accepts a signature from **either** `address(bytes20(sender))` (the owner) **or** `getLinkedSigner(sender)` (the current linked signer):

```solidity
function validateSignature(...) public pure {
    address recovered = ECDSA.recover(digest, signature);
    require(
        (recovered != address(0)) &&
            ((recovered == address(uint160(bytes20(sender)))) ||
                (recovered == linkedSigner)),
        ERR_INVALID_SIGNATURE
    );
}
``` [2](#0-1) 

This means the linked signer can craft and submit a valid `LinkSigner` transaction that writes any new address into `linkedSigners[subaccount]` — including a fresh attacker-controlled address — without the subaccount owner's knowledge or consent. [3](#0-2) 

The **only** on-chain revocation path available to the owner is the slow-mode `LinkSigner` path, which enforces `validateSender(txn.sender, sender)` (i.e., `msg.sender` must be the owner's EOA) and carries a hardcoded `SLOW_MODE_TX_DELAY` of three days:

```solidity
} else if (txType == IEndpoint.TransactionType.LinkSigner) {
    ...
    validateSender(txn.sender, sender);   // only owner can use slow path
    linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
``` [4](#0-3) 

```solidity
slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
    executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY,
    ...
});
``` [5](#0-4) 

During that three-day window, the malicious linked signer (or its successor) can submit a fast-path `LinkSigner` transaction through the sequencer to re-link itself before the owner's slow-mode revocation is executed, creating a persistent, irrevocable delegation.

---

### Impact Explanation

Once a linked signer self-replaces, it (or its successor) retains the ability to sign every operation that accepts `allowLinkedSigner = true`:

| Transaction | `allowLinkedSigner` |
|---|---|
| `WithdrawCollateral` | `true` |
| `MintNlp` / `BurnNlp` | `true` |
| `TransferQuote` | `true` |
| `LiquidateSubaccount` | `true` |
| `LinkSigner` (this bug) | `true` | [6](#0-5) [7](#0-6) [8](#0-7) 

Concrete corrupted state: `linkedSigners[subaccount]` is permanently set to an attacker-controlled address. The attacker can drain the subaccount's collateral via `WithdrawCollateral`, redirect quote balances via `TransferQuote`, or manipulate NLP positions via `MintNlp`/`BurnNlp` — all while the owner is locked out of revoking the delegation.

---

### Likelihood Explanation

The trigger requires the linked signer to be compromised or malicious. Linked signers are session keys commonly used by trading bots, front-ends, or third-party integrations — a realistic attack surface. Once a session key is compromised (e.g., leaked from a hot wallet or a front-end), the attacker can immediately self-replace before the owner reacts. The 3-day slow-mode delay makes the race condition heavily favourable to the attacker. Likelihood is **medium**: the precondition (compromised session key) is realistic, and the protocol provides no fast revocation path for the owner.

---

### Recommendation

Change `allowLinkedSigner` to `false` in the `LinkSigner` transaction handler in `processTransactionImpl`. Only the subaccount owner should be permitted to change the linked signer:

```solidity
// Before (vulnerable):
validateSignedTx(signedTx.tx.sender, signedTx.tx.nonce, transaction, signedTx.signature, true);

// After (fixed):
validateSignedTx(signedTx.tx.sender, signedTx.tx.nonce, transaction, signedTx.signature, false);
``` [9](#0-8) 

This is consistent with the slow-mode `LinkSigner` path, which already enforces owner-only access via `validateSender`. Additionally, consider adding a fast-path owner-only revocation mechanism (e.g., a direct `unlinkSigner` function callable by the owner's EOA without sequencer involvement) to eliminate the 3-day revocation window.

---

### Proof of Concept

1. Owner of subaccount `S` submits a `LinkSigner` transaction linking session key `A` (e.g., a trading bot).
2. Session key `A` is compromised by an attacker.
3. Attacker uses key `A` to sign a `LinkSigner` transaction for subaccount `S`, setting `signedTx.tx.signer = bytes32(uint256(uint160(attackerAddress)))`.
4. The sequencer includes this transaction; `linkedSigners[S]` is now set to `attackerAddress`.
5. Owner detects the compromise and submits a slow-mode `LinkSigner` to set `linkedSigners[S] = address(0)`. This is queued with a 3-day delay.
6. Before the 3-day window expires, the attacker (now holding `linkedSigners[S]`) signs another fast-path `LinkSigner` transaction to re-link `attackerAddress` (or a new address).
7. The sequencer includes the attacker's transaction; the owner's slow-mode revocation is rendered ineffective.
8. The attacker now signs a `WithdrawCollateral` transaction (accepted because `allowLinkedSigner = true`) to drain all collateral from subaccount `S`. [10](#0-9) [11](#0-10)

### Citations

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

**File:** core/contracts/EndpointTx.sol (L376-380)
```text
        slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: transaction
        });
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

**File:** core/contracts/EndpointTx.sol (L534-545)
```text
        } else if (txType == IEndpoint.TransactionType.MintNlp) {
            IEndpoint.SignedMintNlp memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedMintNlp)
            );
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

**File:** core/contracts/EndpointStorage.sol (L38-39)
```text
    IEndpoint.SlowModeConfig internal slowModeConfig;
    mapping(uint64 => IEndpoint.SlowModeTx) internal slowModeTxs;
```

**File:** core/contracts/EndpointStorage.sol (L50-50)
```text
    mapping(bytes32 => address) internal linkedSigners;
```
