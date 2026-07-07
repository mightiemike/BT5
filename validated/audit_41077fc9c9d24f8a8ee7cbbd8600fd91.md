### Title
Linked Signer Can Overwrite Its Own Authority via `LinkSigner` Transaction — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

The `LinkSigner` handler in `processTransactionImpl` calls `validateSignedTx` with `allowLinkedSigner = true`, which permits the **current linked signer** — not just the subaccount owner — to sign a transaction that replaces the linked signer with any arbitrary address. A compromised linked signer can therefore install an attacker-controlled address as the new linked signer, gaining persistent full signing authority over the subaccount and enabling direct asset theft.

---

### Finding Description

In `EndpointTx.sol`, the `LinkSigner` branch of `processTransactionImpl` (lines 576–590) validates the incoming transaction with `allowLinkedSigner = true`:

```solidity
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
``` [1](#0-0) 

`validateSignedTx` with `allowLinkedSigner = true` resolves the permitted signer as either the subaccount owner **or** `linkedSigners[sender]`:

```solidity
verifier.validateSignature(
    sender,
    allowLinkedSigner ? getLinkedSigner(sender) : address(0),
    digest,
    signature
);
``` [2](#0-1) 

`linkedSigners` is the security-critical mapping that determines who holds delegated signing authority over a subaccount for all subsequent operations including withdrawals:

```solidity
mapping(bytes32 => address) internal linkedSigners;
``` [3](#0-2) 

Because `allowLinkedSigner = true` is passed for the `LinkSigner` transaction type itself, the current linked signer can sign a `LinkSigner` transaction that overwrites `linkedSigners[subaccount]` with any address — including an attacker-controlled one. There is no check that the signer of a `LinkSigner` transaction is the subaccount owner rather than the current delegate.

This is the direct analog to the Wormhole bug: in Wormhole, `register_emitter` unconditionally overwrote the sequence number without checking whether the emitter was already registered. Here, `LinkSigner` unconditionally overwrites the linked signer without restricting the operation to the subaccount owner.

---

### Impact Explanation

A compromised linked signer (e.g., a leaked API key or hot wallet) can:

1. Sign a `SignedLinkSigner` transaction setting `signer = attacker_wallet`.
2. The sequencer processes it; `validateSignedTx` passes because the current linked signer is an accepted signer.
3. `linkedSigners[alice_sub]` is now `attacker_wallet`.
4. The attacker immediately uses `attacker_wallet` to sign a `WithdrawCollateral` or `WithdrawCollateralV2` transaction (both accept `allowLinkedSigner = true`), draining all collateral from the subaccount. [4](#0-3) 

The result is **complete, direct loss of all assets** in the subaccount. The original owner may not detect the linked signer substitution until funds are already gone.

---

### Likelihood Explanation

Medium. Linked signers are by design hot wallets or programmatic API keys used for automated trading — they are more exposed than the cold-wallet owner key. A server compromise, leaked `.env` file, or stolen API key is a realistic attacker precondition. No privileged protocol access is required; the attacker only needs the linked signer's private key.

---

### Recommendation

Pass `allowLinkedSigner = false` for the `LinkSigner` transaction type in `processTransactionImpl`. Only the subaccount owner (the address encoded in the first 20 bytes of the `bytes32` subaccount) should be permitted to change the linked signer. The fix is a one-character change at the call site:

```solidity
// Before
validateSignedTx(signedTx.tx.sender, signedTx.tx.nonce, transaction, signedTx.signature, true);

// After
validateSignedTx(signedTx.tx.sender, signedTx.tx.nonce, transaction, signedTx.signature, false);
``` [5](#0-4) 

---

### Proof of Concept

1. Alice owns subaccount `alice_sub` (`alice_address || "default"`).
2. Alice submits a `LinkSigner` transaction linking `hot_wallet` as her delegate.
3. Attacker obtains `hot_wallet`'s private key (e.g., leaked API key).
4. Attacker constructs a `SignedLinkSigner` with `sender = alice_sub`, `signer = attacker_wallet`, and the correct nonce (`nonces[alice_address]`).
5. Attacker signs the EIP-712 digest with `hot_wallet` and submits to the sequencer.
6. Sequencer calls `processTransactionImpl`; `validateSignedTx(..., true)` passes because `hot_wallet == linkedSigners[alice_sub]`.
7. `linkedSigners[alice_sub]` is overwritten with `attacker_wallet`.
8. Attacker signs a `WithdrawCollateralV2` transaction with `sendTo = attacker_address` using `attacker_wallet`; `allowLinkedSigner` is `false` only when `sendTo != address(0)` — but the attacker can use the original `WithdrawCollateral` path which accepts `allowLinkedSigner = true` unconditionally, or set `sendTo = address(0)` to withdraw to the subaccount's own address and then transfer out.
9. All of Alice's collateral is drained. [6](#0-5)

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

**File:** core/contracts/EndpointStorage.sol (L50-50)
```text
    mapping(bytes32 => address) internal linkedSigners;
```
