### Title
Linked Signer Can Self-Replace Without Subaccount Owner Re-Authentication — (`File: core/contracts/EndpointTx.sol`)

---

### Summary

The `LinkSigner` transaction type is processed with `allowLinkedSigner = true`, meaning the currently registered linked signer can sign a `LinkSigner` transaction to replace itself with any arbitrary address — including an attacker-controlled one — without any involvement or re-authentication from the subaccount owner. This is a privilege-escalation path: a compromised hot-wallet linked signer can permanently hijack full signing authority over a subaccount.

---

### Finding Description

In `EndpointTx.sol`, the sequencer-path handler for `TransactionType.LinkSigner` calls `validateSignedTx` with the fifth argument hardcoded to `true` (the `allowLinkedSigner` flag):

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
        true          // ← allowLinkedSigner = true
    );
    linkedSigners[signedTx.tx.sender] = address(
        uint160(bytes20(signedTx.tx.signer))
    );
}
``` [1](#0-0) 

`validateSignedTx` delegates to `validateSignature`, which passes `getLinkedSigner(sender)` to `Verifier.validateSignature` when `allowLinkedSigner` is `true`:

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

`Verifier.validateSignature` accepts either the subaccount owner address **or** the linked signer as a valid signer:

```solidity
// Verifier.sol lines 291–304
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
``` [3](#0-2) 

The result: a linked signer can sign a `LinkSigner` transaction that writes a new address into `linkedSigners[subaccount]`, overwriting itself with an attacker-controlled address, with no participation from the subaccount owner. [4](#0-3) 

The `LinkSigner` operation is structurally a **credential-rotation** action — it changes who is authorized to act on behalf of a subaccount. All other credential-rotation or high-privilege operations in the protocol require the subaccount owner's key directly. Allowing the existing linked signer to perform this operation without owner re-authentication breaks the intended trust hierarchy. [5](#0-4) 

---

### Impact Explanation

Once the attacker controls the linked signer slot, they can sign any transaction that accepts `allowLinkedSigner = true`, including:

- `WithdrawCollateral` (lines 418–436) — drain all collateral from the subaccount
- `WithdrawCollateralV2` (lines 442–465, when `sendTo == address(0)`) — drain to any address
- `TransferQuote` (lines 599–614) — transfer quote balance to an attacker-controlled subaccount
- `MintNlp` / `BurnNlp` (lines 539–573) — manipulate NLP positions
- `LiquidateSubaccount` (lines 397–412) — initiate liquidations from the victim's subaccount [6](#0-5) [7](#0-6) 

The subaccount owner has no on-chain mechanism to detect or prevent this before the attacker acts. The owner's only recourse is to submit a new `LinkSigner` transaction themselves, but by then the attacker already controls the signing slot and can front-run or drain assets first.

**Corrupted state:** `linkedSigners[subaccount]` is permanently set to an attacker-controlled address, granting full signing authority over the subaccount's collateral, positions, and transfers. [4](#0-3) 

---

### Likelihood Explanation

Linked signers are by design hot wallets — API keys, trading bots, or session keys used for automated order placement. Hot wallets have a materially higher compromise probability than cold wallets (the subaccount owner's primary key). Common compromise vectors include:

- Malware or keyloggers on a trading server
- Leaked API keys from misconfigured environment variables or logs
- Phishing of a bot operator's credentials

Once a linked signer key is obtained, the attacker needs only to craft and submit a single `LinkSigner` transaction through the sequencer. No admin access, no governance capture, and no additional on-chain state is required. The attack is silent — the subaccount owner sees no on-chain event distinguishing a legitimate linked-signer rotation from a malicious one.

---

### Recommendation

Change the `allowLinkedSigner` flag for `LinkSigner` transactions from `true` to `false`. The `LinkSigner` operation modifies signing authority and must require the subaccount owner's key exclusively:

```solidity
// EndpointTx.sol — LinkSigner handler
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // owner-only: linked signer must not self-replace
);
``` [8](#0-7) 

This mirrors the principle from the external report: sensitive operations that modify credentials or signing authority must require re-authentication from the highest-privilege key, not merely the delegated key.

---

### Proof of Concept

1. Subaccount `alice` has `linkedSigners[alice] = hotWallet`.
2. Attacker compromises `hotWallet` private key (e.g., via malware on a trading server).
3. Attacker constructs:
   ```
   LinkSigner {
       sender: alice,
       signer: attacker_address,
       nonce: current_nonce(alice)
   }
   ```
4. Attacker signs the EIP-712 digest with `hotWallet`.
5. Attacker submits the transaction to the sequencer.
6. Sequencer calls `processTransactionImpl` → `LinkSigner` branch → `validateSignedTx(..., true)`.
7. `Verifier.validateSignature` recovers `hotWallet` from the signature; `hotWallet == getLinkedSigner(alice)` → passes.
8. `linkedSigners[alice]` is overwritten with `attacker_address`.
9. Attacker immediately signs a `WithdrawCollateral` transaction with `attacker_address`, draining `alice`'s collateral. [1](#0-0) [9](#0-8)

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
