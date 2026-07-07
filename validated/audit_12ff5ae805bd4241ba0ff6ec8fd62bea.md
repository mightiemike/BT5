### Title
Linked Signer Can Replace Itself via `LinkSigner` Transaction, Enabling Full Subaccount Takeover and Fund Theft — (`core/contracts/EndpointTx.sol`)

---

### Summary

The `LinkSigner` transaction type in `EndpointTx.sol` is validated with `allowLinkedSigner = true`, meaning the currently registered `linkedSigner` of a subaccount can sign a `LinkSigner` transaction to replace itself with any attacker-controlled address. This is the direct EVM analog of the NEAR `assert_one_yocto()` vulnerability class: a delegated key (the `linkedSigner`, analogous to NEAR's `Function Call` key) is permitted to execute a privileged operation that should be restricted to the full account owner (analogous to NEAR's `Full Access` key). Once the linked signer is replaced, the attacker-controlled address can sign `TransferQuote` transactions (also `allowLinkedSigner = true`) to drain the victim's entire quote balance to an attacker-controlled subaccount.

---

### Finding Description

In `EndpointTx.processTransactionImpl`, the `LinkSigner` branch at lines 576–590 calls `validateSignedTx` with the fifth argument hardcoded to `true`:

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
        true                          // ← allowLinkedSigner = true
    );
    linkedSigners[signedTx.tx.sender] = address(
        uint160(bytes20(signedTx.tx.signer))
    );
``` [1](#0-0) 

`validateSignedTx` with `allowLinkedSigner = true` passes `getLinkedSigner(sender)` as the accepted alternate signer to `verifier.validateSignature`:

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

`Verifier.validateSignature` accepts the signature if it recovers to either the subaccount owner **or** the linked signer:

```solidity
require(
    (recovered != address(0)) &&
        ((recovered == address(uint160(bytes20(sender)))) ||
            (recovered == linkedSigner)),
    ERR_INVALID_SIGNATURE
);
``` [3](#0-2) 

Therefore, the current `linkedSigner` can produce a valid signature for a `LinkSigner` transaction that writes any new address into `linkedSigners[subaccount]`.

The `TransferQuote` branch is also validated with `allowLinkedSigner = true`:

```solidity
} else if (txType == IEndpoint.TransactionType.TransferQuote) {
    ...
    validateSignedTx(
        signedTx.tx.sender,
        signedTx.tx.nonce,
        transaction,
        signedTx.signature,
        true                          // ← allowLinkedSigner = true
    );
    ...
    clearinghouse.transferQuote(signedTx.tx);
``` [4](#0-3) 

This means the attacker-installed linked signer can immediately transfer the victim's entire quote balance to any recipient subaccount the attacker controls.

The protocol designers already recognized this class of risk for `WithdrawCollateralV2`: the linked signer is only permitted when `sendTo == address(0)` (i.e., funds return to the owner's own address), and the full owner signature is required when `sendTo` is a custom address:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    signedTx.tx.sendTo == address(0)   // ← restricted
);
``` [5](#0-4) 

This partial mitigation was not applied to `LinkSigner` or `TransferQuote`.

---

### Impact Explanation

**Concrete state delta:** `linkedSigners[victim_subaccount]` is overwritten with an attacker-controlled address. The attacker then signs a `TransferQuote` transaction moving the victim's entire quote balance to an attacker-owned subaccount, from which it is withdrawn via `WithdrawCollateral`. All collateral denominated in the quote asset is permanently lost by the victim.

**Severity:** High. The `linkedSigner` mechanism is the primary way users authorize trading bots and API integrations. Any such integration that is compromised, malicious, or operates under a leaked key can execute this attack silently in a single sequencer batch without any on-chain warning to the victim.

---

### Likelihood Explanation

Users of Nado are expected to set `linkedSigner` for automated trading, API access, or bot integrations. This is a standard, documented protocol flow. A compromised API key, a malicious third-party integration, or a phishing site that obtains the linked signer key can trigger this attack against any subaccount that has a linked signer registered. No privileged protocol access is required — only possession of the victim's current linked signer key.

---

### Recommendation

`LinkSigner` must require the full subaccount owner signature. Change `allowLinkedSigner` to `false` for the `LinkSigner` branch:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // ← must require full owner key; linked signer must not self-replace
);
``` [6](#0-5) 

Additionally, `TransferQuote` should be evaluated for the same restriction. Because it transfers funds to an arbitrary recipient, it is analogous to `WithdrawCollateralV2` with a custom `sendTo` address, and should require the full owner signature:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // ← full owner key required for cross-subaccount fund transfer
);
``` [7](#0-6) 

---

### Proof of Concept

**Setup:**
- Alice owns subaccount `A` with 10,000 USDC quote balance.
- Alice registers `linkedSigner[A] = bot_key` for automated trading.
- Attacker obtains `bot_key` (compromised API, leaked env var, malicious integration).

**Attack steps:**

1. Attacker crafts a `LinkSigner` transaction:
   - `sender = A`, `signer = attacker_address`, `nonce = current_nonce[A]`
   - Signs with `bot_key` (currently valid linked signer)
   - Submits to sequencer

2. Sequencer processes the transaction. `validateSignedTx(..., true)` accepts `bot_key`'s signature. `linkedSigners[A]` is overwritten with `attacker_address`. [1](#0-0) 

3. Attacker crafts a `TransferQuote` transaction:
   - `sender = A`, `recipient = attacker_subaccount`, `amount = 10000e18`, `nonce = current_nonce[A]`
   - Signs with `attacker_address` (now the registered linked signer)
   - Submits to sequencer

4. Sequencer processes the transaction. `validateSignedTx(..., true)` accepts `attacker_address`'s signature. `clearinghouse.transferQuote` moves 10,000 USDC from `A` to `attacker_subaccount`. [4](#0-3) 

5. Attacker submits `WithdrawCollateral` from `attacker_subaccount` to withdraw 10,000 USDC to their wallet.

**Result:** Alice's entire quote balance is stolen. Alice retains ownership of subaccount `A` but it is drained. The attack requires only the linked signer key — no privileged protocol access.

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

**File:** core/contracts/EndpointTx.sol (L442-448)
```text
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                signedTx.tx.sendTo == address(0)
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
