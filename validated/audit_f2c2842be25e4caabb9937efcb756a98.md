### Title
Linked Signer Can Transfer Quote Balance to Arbitrary Recipient — (`core/contracts/EndpointTx.sol`)

---

### Summary

The `TransferQuote` transaction type in `EndpointTx.processTransactionImpl` unconditionally permits a linked signer to sign and execute quote transfers to any arbitrary recipient subaccount. This grants the linked signer — expected by users to be a scoped trading bot — the additional power to drain a subaccount's entire quote balance to an attacker-controlled subaccount.

---

### Finding Description

In `EndpointTx.processTransactionImpl`, the `TransferQuote` branch calls `validateSignedTx` with `allowLinkedSigner = true`:

```solidity
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
        true                          // ← allowLinkedSigner = true
    );
    ...
    clearinghouse.transferQuote(signedTx.tx);
``` [1](#0-0) 

The `recipient` field in `TransferQuote` is an arbitrary subaccount — there is no restriction that it must be the subaccount owner's own address. A linked signer can therefore craft a `TransferQuote` transaction moving the victim's entire quote balance to any subaccount the attacker controls.

This is directly inconsistent with how the protocol treats `WithdrawCollateralV2`, where `allowLinkedSigner` is explicitly gated on `sendTo == address(0)` — i.e., the linked signer is blocked from directing funds to an arbitrary external address:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    signedTx.tx.sendTo == address(0)   // ← linked signer blocked when sendTo != 0
);
``` [2](#0-1) 

The protocol already recognizes that a linked signer must not be allowed to redirect funds to arbitrary addresses in the withdrawal path, but this same guard is absent for `TransferQuote`.

The linked signer is registered per-subaccount in `linkedSigners`: [3](#0-2) 

and resolved in `validateSignature` when `allowLinkedSigner = true`: [4](#0-3) 

---

### Impact Explanation

A malicious or compromised linked signer can drain the full quote balance of any subaccount it is authorized for, by signing a `TransferQuote` transaction with `recipient` set to an attacker-controlled subaccount. The attacker then withdraws those funds via a normal `WithdrawCollateral` flow. The corrupted state is the victim subaccount's quote balance in `SpotEngine`, which is decremented by the full transfer amount with no recourse.

---

### Likelihood Explanation

Linked signers are a standard, documented feature of the Nado protocol used by traders to delegate order signing to automated bots or API keys. Any user who has set a linked signer is exposed. A malicious operator of a trading bot service, or an attacker who compromises a linked signer key, can exploit this without any additional privilege — the only requirement is possession of the linked signer's private key, which is a realistic threat model for API-key-based trading infrastructure.

---

### Recommendation

Apply the same guard used in `WithdrawCollateralV2`: set `allowLinkedSigner = false` for `TransferQuote`, requiring the subaccount owner's direct signature for any quote transfer. If linked signers must be permitted to transfer quote between their own subaccounts (e.g., to isolated subaccounts), restrict the `recipient` to subaccounts whose owner address matches the sender's owner address, mirroring the `WithdrawCollateralV2` pattern.

---

### Proof of Concept

1. Alice sets Eve's address as the linked signer for her subaccount (normal usage — e.g., for automated market-making).
2. Eve constructs a `TransferQuote` transaction: `sender = Alice's subaccount`, `recipient = Eve's own subaccount`, `amount = Alice's full quote balance`.
3. Eve signs the transaction with her linked signer key.
4. The sequencer processes the transaction; `validateSignedTx` accepts Eve's signature because `allowLinkedSigner = true` for `TransferQuote`.
5. `clearinghouse.transferQuote` moves Alice's entire quote balance to Eve's subaccount.
6. Eve submits a `WithdrawCollateral` transaction from her own subaccount and receives the funds on-chain.
7. Alice's subaccount balance is zero; Eve has vanished with the funds.

### Citations

**File:** core/contracts/EndpointTx.sol (L177-183)
```text
    ) internal virtual {
        verifier.validateSignature(
            sender,
            allowLinkedSigner ? getLinkedSigner(sender) : address(0),
            digest,
            signature
        );
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

**File:** core/contracts/EndpointStorage.sol (L50-50)
```text
    mapping(bytes32 => address) internal linkedSigners;
```
