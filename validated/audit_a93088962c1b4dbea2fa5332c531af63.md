### Title
Linked Signer Can Overwrite Subaccount's `linkedSigners` Entry Without Owner Authorization — (`File: core/contracts/EndpointTx.sol`)

### Summary
The `LinkSigner` fast-path transaction in `EndpointTx.processTransactionImpl` is validated with `allowLinkedSigner = true`. This means the **current linked signer** of a subaccount can sign and submit a `LinkSigner` transaction to replace the linked signer with any arbitrary address — without the subaccount owner's knowledge or consent. The subaccount owner only authorized the linked signer for trading operations, not for mutating the subaccount's own signer configuration.

### Finding Description

In `EndpointTx.sol`, the fast-path handler for `TransactionType.LinkSigner` calls `validateSignedTx` with `allowLinkedSigner = true`: [1](#0-0) 

`validateSignedTx` delegates to `validateSignature`, which passes `getLinkedSigner(sender)` as the accepted alternate signer when `allowLinkedSigner` is `true`: [2](#0-1) 

`getLinkedSigner` resolves the linked signer for both normal and isolated subaccounts: [3](#0-2) 

Because the linked signer's signature is accepted for a `LinkSigner` transaction, the linked signer can craft a valid `SignedLinkSigner` payload naming any address as the new signer, submit it to the sequencer, and the on-chain check will pass. The result is:

```solidity
linkedSigners[signedTx.tx.sender] = address(uint160(bytes20(signedTx.tx.signer)));
```

The subaccount owner's intended linked signer is silently replaced.

By contrast, the **slow-path** `LinkSigner` handler correctly uses `validateSender`, which enforces `msg.sender == address(uint160(bytes20(txn.sender)))` — only the actual Ethereum owner of the subaccount can set the linked signer via slow mode: [4](#0-3) 

The fast path lacks this ownership binding, creating the asymmetry.

### Impact Explanation

Once the attacker-controlled address is installed as the new linked signer, it can sign any transaction type that accepts `allowLinkedSigner = true`, including:

- **`TransferQuote`** (lines 593–614): transfers the victim's quote balance to any `recipient` address, draining the subaccount.
- **`WithdrawCollateral` V1** (lines 413–436): withdraws collateral (sends to the owner's address, so limited direct theft, but can be used to force deleveraging).
- **`MintNlp` / `BurnNlp`** (lines 530–573): manipulates the victim's NLP position.

The `TransferQuote` path is the most severe: the attacker-controlled linked signer can drain the entire quote balance of the victim subaccount to an arbitrary `recipient`: [5](#0-4) 

### Likelihood Explanation

Linked signers are a standard feature in DEX protocols used by market makers, bots, and institutional traders. Any party that has been granted linked-signer access (e.g., a trading bot, a third-party service) can exploit this to escalate their access. The sequencer will include any validly signed transaction, so no sequencer compromise is required. The attack is silent and does not require the victim to take any action.

### Recommendation

Change `allowLinkedSigner` to `false` for the `LinkSigner` transaction type in the fast path, consistent with the slow-path behavior:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false  // only the subaccount owner may change the linked signer
);
```

This ensures that only the actual subaccount owner (whose address is embedded in `signedTx.tx.sender`) can authorize a change to the linked signer, matching the invariant enforced by the slow-mode path.

### Proof of Concept

1. Alice owns subaccount `aliceSub` and sets Bob (`bobAddr`) as her linked signer via a legitimate `LinkSigner` transaction.
2. Bob constructs a `SignedLinkSigner` payload: `{ sender: aliceSub, signer: attackerAddr, nonce: currentNonce }` and signs it with `bobAddr`'s key.
3. Bob submits this to the sequencer. On-chain, `validateSignedTx(..., true)` accepts Bob's signature because `getLinkedSigner(aliceSub) == bobAddr`.
4. `linkedSigners[aliceSub]` is now set to `attackerAddr`.
5. The attacker constructs a `SignedTransferQuote` payload: `{ sender: aliceSub, recipient: attackerSubaccount, amount: fullBalance, nonce: nextNonce }` and signs it with `attackerAddr`'s key.
6. The sequencer includes this transaction; `validateSignedTx(..., true)` accepts `attackerAddr`'s signature as the new linked signer.
7. Alice's entire quote balance is transferred to the attacker's subaccount.

### Citations

**File:** core/contracts/EndpointTx.sol (L143-157)
```text
    function getLinkedSigner(bytes32 subaccount)
        public
        view
        virtual
        returns (address)
    {
        return
            RiskHelper.isIsolatedSubaccount(subaccount)
                ? linkedSigners[
                    IOffchainExchange(offchainExchange).getParentSubaccount(
                        subaccount
                    )
                ]
                : linkedSigners[subaccount];
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
