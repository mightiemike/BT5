### Title
Linked Signer Can Permanently Hijack Subaccount by Re-Linking to Attacker-Controlled Address - (File: `core/contracts/EndpointTx.sol`)

---

### Summary

The `LinkSigner` fast-path handler in `EndpointTx.processTransactionImpl` passes `allowLinkedSigner = true` to `validateSignedTx`. This means the currently linked signer — a delegated session key or automation key — can sign a new `LinkSigner` transaction that overwrites `linkedSigners[subaccount]` with an attacker-controlled address. Once rebound, the attacker's key satisfies every subsequent signature check on that subaccount, enabling full collateral drainage.

---

### Finding Description

In `EndpointTx.processTransactionImpl`, the `LinkSigner` branch is:

```solidity
// EndpointTx.sol:576-590
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
        true          // ← allowLinkedSigner = true
    );
    linkedSigners[signedTx.tx.sender] = address(
        uint160(bytes20(signedTx.tx.signer))
    );
```

`validateSignedTx` calls `validateSignature`, which calls `verifier.validateSignature`:

```solidity
// Verifier.sol:297-303
address recovered = ECDSA.recover(digest, signature);
require(
    (recovered != address(0)) &&
        ((recovered == address(uint160(bytes20(sender)))) ||
            (recovered == linkedSigner)),   // ← linked signer accepted
    ERR_INVALID_SIGNATURE
);
```

Because `allowLinkedSigner = true` is passed, the currently stored `linkedSigners[subaccount]` is fetched and accepted as a valid signer for the `LinkSigner` transaction itself. There is no guard that restricts the authority to change the linked signer to the subaccount owner only.

The slow-mode path (`processSlowModeTransactionImpl`) does **not** share this flaw: it uses `validateSender(txn.sender, sender)`, which enforces that `msg.sender` equals the first-20-byte owner address embedded in the subaccount identifier, so the linked signer cannot exploit that path. [1](#0-0) [2](#0-1) [3](#0-2) 

---

### Impact Explanation

`linkedSigners` is the sole alternative signing authority accepted by `validateSignature` for every high-value transaction type: `WithdrawCollateral`, `WithdrawCollateralV2`, `TransferQuote`, `MintNlp`, `BurnNlp`, `LiquidateSubaccount`, and order matching. Once an attacker rebinds `linkedSigners[victimSubaccount]` to an address they control, they can sign any of those transactions and drain all collateral held by the subaccount. The rebinding persists indefinitely; there is no automatic expiry or revocation mechanism. [4](#0-3) [5](#0-4) 

---

### Likelihood Explanation

Linked signers are explicitly designed for session keys and automation operators — keys stored in hot wallets, browser extensions, or automated bots. These are materially more likely to be compromised than the cold-wallet owner key. Any party that obtains the linked signer's private key (phishing, leaked `.env`, compromised bot) can immediately submit a `LinkSigner` transaction to the sequencer signed by that key. The sequencer's on-chain validation (`validateSignedTx` with `allowLinkedSigner = true`) will accept it. No owner interaction or special privilege is required beyond possession of the linked signer key. [6](#0-5) 

---

### Recommendation

Change `allowLinkedSigner` to `false` for the `LinkSigner` branch in `processTransactionImpl`. Only the subaccount owner (the address embedded in the first 20 bytes of the subaccount identifier) should be permitted to change the linked signer.

```diff
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
-         true
+         false   // only owner may rebind the linked signer
      );
      linkedSigners[signedTx.tx.sender] = address(
          uint160(bytes20(signedTx.tx.signer))
      );
``` [1](#0-0) 

---

### Proof of Concept

1. **Setup.** User A owns subaccount `subA = abi.encodePacked(addrA, bytes12("default"))`. A links session key `signerB` via a legitimate `LinkSigner` transaction signed by `addrA`. Now `linkedSigners[subA] = signerB`.

2. **Attacker obtains `signerB`.** The session key is leaked (phishing, compromised bot, `.env` exposure).

3. **Rebind.** The attacker crafts:
   ```
   LinkSigner { sender: subA, signer: bytes32(attackerAddr), nonce: currentNonce(addrA) }
   ```
   and signs the EIP-712 digest with `signerB`'s private key. This signature passes `validateSignature` because `recovered == signerB == linkedSigners[subA]` and `allowLinkedSigner = true`.

4. **Sequencer includes the transaction.** The sequencer has no on-chain obligation to reject a validly signed `LinkSigner` transaction. After inclusion, `linkedSigners[subA] = attackerAddr`.

5. **Drain.** The attacker signs a `WithdrawCollateral` transaction for the full balance of `subA` using `attackerAddr`'s key. `validateSignature` recovers `attackerAddr`, which now equals `linkedSigners[subA]`, so the check passes and the collateral is withdrawn to the attacker. [2](#0-1) [7](#0-6)

### Citations

**File:** core/contracts/EndpointTx.sol (L86-106)
```text
    function validateSignedTx(
        bytes32 sender,
        uint64 nonce,
        bytes calldata transaction,
        bytes memory signature,
        bool allowLinkedSigner
    ) internal {
        validateNonce(sender, nonce);
        validateSignature(
            sender,
            _hashTypedDataV4(
                computeDigest(
                    IEndpoint.TransactionType(uint8(transaction[0])),
                    transaction[1:]
                )
            ),
            signature,
            allowLinkedSigner
        );
        requireSubaccount(sender);
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

**File:** core/contracts/EndpointStorage.sol (L50-50)
```text
    mapping(bytes32 => address) internal linkedSigners;
```
