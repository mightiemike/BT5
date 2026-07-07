### Title
Linked Signer Can Unilaterally Rotate Itself to an Attacker-Controlled Address via Fast-Mode `LinkSigner` — (`File: core/contracts/EndpointTx.sol`)

---

### Summary

In `EndpointTx.processTransactionImpl`, the `LinkSigner` fast-mode transaction is validated with `allowLinkedSigner = true`. This means the **current linked signer** — not just the subaccount owner — can sign a valid `LinkSigner` transaction that overwrites `linkedSigners[subaccount]` with any arbitrary address. A compromised or malicious linked signer can silently replace itself with an attacker-controlled address, granting the attacker full signing authority over the subaccount, including the ability to sign `WithdrawCollateral` transactions. The subaccount owner's only recovery path is slow mode, which has a 3-day delay during which funds can be drained.

---

### Finding Description

In `processTransactionImpl`, the `LinkSigner` branch is:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    true   // allowLinkedSigner = true
);
linkedSigners[signedTx.tx.sender] = address(
    uint160(bytes20(signedTx.tx.signer))
);
``` [1](#0-0) 

`validateSignedTx` calls `validateSignature`, which delegates to `Verifier.validateSignature`:

```solidity
function validateSignature(bytes32 sender, address linkedSigner, ...) public pure {
    address recovered = ECDSA.recover(digest, signature);
    require(
        (recovered != address(0)) &&
            ((recovered == address(uint160(bytes20(sender)))) ||
                (recovered == linkedSigner)),
        ERR_INVALID_SIGNATURE
    );
}
``` [2](#0-1) 

When `allowLinkedSigner = true`, the verifier accepts a signature from **either** the subaccount owner address **or** the current `linkedSigners[sender]`. Because the `LinkSigner` transaction itself is processed with this flag, the current linked signer can produce a valid signature over a `LinkSigner` payload that sets `signedTx.tx.signer` to any attacker address.

The broken invariant is confirmed by the **slow-mode** path for `LinkSigner`, which enforces strict owner-only authorization via `validateSender`:

```solidity
validateSender(txn.sender, sender);   // requires msg.sender == address(bytes20(txn.sender))
requireSubaccount(txn.sender);
linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
``` [3](#0-2) 

`validateSender` enforces `address(uint160(bytes20(txSender))) == sender`, meaning only the wallet whose address is embedded in the subaccount bytes32 can submit a slow-mode `LinkSigner`. The fast-mode path has no equivalent restriction, creating an asymmetry: the owner is the only authorized party in slow mode, but the linked signer is equally authorized in fast mode. [4](#0-3) 

Once the attacker's address is installed as the linked signer, `WithdrawCollateral` and `WithdrawCollateralV2` — both processed with `allowLinkedSigner = true` — accept signatures from the attacker: [5](#0-4) [6](#0-5) 

---

### Impact Explanation

The attacker gains the ability to sign any transaction type that accepts a linked signer — including `WithdrawCollateral`, `WithdrawCollateralV2`, `LiquidateSubaccount`, `TransferQuote`, `MintNlp`, and `BurnNlp` — on behalf of the victim subaccount. This enables direct, complete theft of all collateral held in the subaccount. The victim's only on-chain recovery path is a slow-mode `LinkSigner` transaction, which is subject to a `SLOW_MODE_TX_DELAY` (hardcoded to 3 days per the comment at line 377), during which the attacker can drain all assets. [7](#0-6) 

---

### Likelihood Explanation

Linked signers are session keys, typically held in browser wallets, hot wallets, or automated bots — environments with materially higher compromise risk than a user's primary cold wallet. Any party that obtains the linked signer's private key (via phishing, malware, or key leakage from an automated system) can execute this attack silently and without any on-chain warning to the victim. The attack requires no special privileges, no admin access, and no sequencer cooperation beyond normal transaction processing.

---

### Recommendation

Process `LinkSigner` in fast mode with `allowLinkedSigner = false`, requiring the signature to come exclusively from the subaccount owner address (the first 20 bytes of the `sender` bytes32). This aligns fast-mode behavior with the slow-mode path, which already enforces owner-only authorization for this operation. A linked signer should be authorized to act *on behalf of* the owner for trading and withdrawal operations, but should never be authorized to *replace itself* or *change the authorization state* of the subaccount.

---

### Proof of Concept

1. User A (address `0xAAAA`) owns subaccount `0xAAAA000000000000000000000000000000000001` and has linked signer B (`0xBBBB`) via a prior `LinkSigner` transaction.
2. Signer B is compromised. The attacker controlling B constructs a `SignedLinkSigner` payload: `sender = 0xAAAA...0001`, `signer = 0xCCCC...0000` (attacker-controlled), `nonce = current_nonce_of_0xAAAA`.
3. The attacker signs this payload with key B. The signature is valid because `Verifier.validateSignature` accepts `recovered == linkedSigner` (`0xBBBB`).
4. The sequencer includes this transaction in a `submitTransactions` batch. `linkedSigners[0xAAAA...0001]` is overwritten to `0xCCCC`.
5. The attacker immediately signs a `WithdrawCollateralV2` transaction for subaccount `0xAAAA...0001` with key C, specifying `sendTo = attacker_wallet`. This signature is accepted because `recovered == linkedSigner` (`0xCCCC`).
6. `clearinghouse.withdrawCollateral` is called, transferring the victim's collateral to the attacker.
7. User A submits a slow-mode `LinkSigner` to revoke the attacker, but the 3-day delay means funds are already gone. [1](#0-0) [8](#0-7) [6](#0-5)

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

**File:** core/contracts/EndpointTx.sol (L374-380)
```text
        IEndpoint.SlowModeConfig memory _slowModeConfig = slowModeConfig;
        requireUnsanctioned(sender);
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
