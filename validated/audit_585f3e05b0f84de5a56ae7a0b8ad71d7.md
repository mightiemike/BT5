### Title
Linked Signer Can Self-Perpetuate by Authorizing Its Own Replacement, Permanently Blocking Owner Revocation - (File: `core/contracts/EndpointTx.sol`)

---

### Summary

The `LinkSigner` fast-path handler in `EndpointTx.processTransactionImpl` passes `allowLinkedSigner = true` to `validateSignedTx`, meaning the **currently registered linked signer** can sign and submit a new `LinkSigner` transaction to replace itself with any address. Because both the subaccount owner and the linked signer compete for the same per-subaccount nonce, a compromised linked signer can race to consume the nonce before the owner's revocation lands, permanently maintaining unauthorized access to the subaccount and all assets it controls.

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

`validateSignedTx` with `allowLinkedSigner = true` resolves to `validateSignature`, which calls `verifier.validateSignature` passing `getLinkedSigner(sender)` as the accepted signer:

```solidity
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
``` [2](#0-1) 

`verifier.validateSignature` accepts a signature from either the subaccount owner address **or** the linked signer:

```solidity
require(
    (recovered != address(0)) &&
        ((recovered == address(uint160(bytes20(sender)))) ||
            (recovered == linkedSigner)),
    ERR_INVALID_SIGNATURE
);
``` [3](#0-2) 

The nonce is keyed on the address embedded in the subaccount `bytes32`:

```solidity
function validateNonce(bytes32 sender, uint64 nonce) internal virtual {
    require(
        nonce == nonces[address(uint160(bytes20(sender)))]++,
        ERR_WRONG_NONCE
    );
}
``` [4](#0-3) 

Both the owner and the linked signer sign on behalf of the same `sender` subaccount, so they share the same nonce counter. Whoever's transaction is sequenced first consumes nonce N and invalidates the other's transaction.

The `linkedSigners` mapping is the persistent authorization credential stored in contract state:

```solidity
mapping(bytes32 => address) internal linkedSigners;
``` [5](#0-4) 

---

### Impact Explanation

Once a linked signer is compromised, the attacker can:

1. Submit a `LinkSigner` transaction (signed by the compromised signer) to replace the linked signer with a new attacker-controlled address before the owner's revocation is sequenced.
2. The owner's revocation transaction (same nonce) is then rejected with `ERR_WRONG_NONCE`.
3. The new linked signer retains full access because `WithdrawCollateral`, `TransferQuote`, `LiquidateSubaccount`, `MintNlp`, and `BurnNlp` all pass `allowLinkedSigner = true`: [6](#0-5) [7](#0-6) 

The attacker can drain all collateral from the subaccount via `WithdrawCollateral` and can repeat the self-perpetuation indefinitely. The subaccount owner has no on-chain mechanism to guarantee revocation without sequencer intervention.

**Impact: 5** — Direct theft of all subaccount collateral; permanent loss of subaccount control.

---

### Likelihood Explanation

The precondition is a compromised linked signer key (phishing, key reuse, leaked hot wallet). This is a realistic scenario for any user who delegates signing to an API key or hot wallet. Once the key is compromised, the race-to-nonce attack requires only that the attacker submit a transaction to the sequencer before the owner's revocation — a low-effort, single-transaction operation. The sequencer is off-chain and processes transactions in submission order, giving the attacker a practical window.

**Likelihood: 3** — Requires a compromised linked signer, but the exploit itself is trivial once that precondition is met.

---

### Recommendation

Remove `allowLinkedSigner = true` from the `LinkSigner` handler. Only the subaccount owner (the address embedded in the `sender` bytes32) should be permitted to change or revoke the linked signer. Change the call to:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // only owner may change linked signer
);
``` [8](#0-7) 

This ensures the linked signer credential can only be set or cleared by the party who owns the subaccount, directly analogous to the recommendation in the reference report to ensure sensitive credentials are cleared only by the authorized principal.

---

### Proof of Concept

**Setup:**
- Subaccount `S` owned by address `owner` (embedded in `S`).
- `owner` links signer `signerA` via a valid `LinkSigner` tx. `linkedSigners[S] = signerA`.
- `signerA`'s private key is compromised by attacker.

**Attack sequence:**

1. `owner` constructs `LinkSigner{sender: S, signer: bytes32(0), nonce: N}` (revocation) and submits to sequencer.
2. Attacker (holding `signerA`) constructs `LinkSigner{sender: S, signer: bytes32(signerB), nonce: N}` and submits to sequencer.
3. Sequencer processes attacker's transaction first. `validateSignedTx` accepts `signerA`'s signature because `allowLinkedSigner = true`. Nonce increments to `N+1`. `linkedSigners[S] = signerB`.
4. Sequencer processes owner's revocation. `validateNonce` fails: expected `N+1`, got `N`. Transaction reverts.
5. Attacker now controls `signerB`. Submits `WithdrawCollateral{sender: S, productId: USDC, amount: MAX, nonce: N+1}` signed by `signerB`. Collateral is drained. [9](#0-8)

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

**File:** core/contracts/EndpointTx.sol (L599-604)
```text
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
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
