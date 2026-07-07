### Title
Linked Signer Can Re-Delegate Subaccount Control to Arbitrary Address — (`core/contracts/EndpointTx.sol`)

---

### Summary

The `LinkSigner` fast-mode transaction handler in `EndpointTx.sol` calls `validateSignedTx` with `allowLinkedSigner = true`. This means the **currently linked signer** — not just the subaccount owner — can sign a new `LinkSigner` transaction to replace themselves with any arbitrary address. A malicious or compromised linked signer can silently re-delegate subaccount control to an attacker, who then gains full signing authority over the subaccount including withdrawals, transfers, and liquidations.

---

### Finding Description

In `EndpointTx.processTransactionImpl`, the `LinkSigner` fast-mode branch is:

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
        true   // <-- allowLinkedSigner = true
    );
    linkedSigners[signedTx.tx.sender] = address(
        uint160(bytes20(signedTx.tx.signer))
    );
}
``` [1](#0-0) 

`validateSignedTx` with `allowLinkedSigner = true` resolves to `validateSignature`, which passes the current linked signer to `Verifier.validateSignature`:

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

The signature is accepted if it comes from **either** the subaccount owner address **or** the currently linked signer. Since `LinkSigner` itself uses `allowLinkedSigner = true`, the currently linked signer can sign a new `LinkSigner` transaction to overwrite `linkedSigners[subaccount]` with any address.

By contrast, the slow-mode `LinkSigner` path correctly enforces that only the subaccount owner can submit it, via `validateSender(txn.sender, sender)` which checks `msg.sender == address(uint160(bytes20(txn.sender)))`: [3](#0-2) 

The fast-mode path has no equivalent restriction — it accepts the linked signer's signature as sufficient authority to re-delegate.

---

### Impact Explanation

Once the attacker's address is installed as the linked signer, they can sign any fast-mode transaction that uses `allowLinkedSigner = true`, including:

- `WithdrawCollateral` / `WithdrawCollateralV2` — drain collateral from the subaccount [4](#0-3) 
- `TransferQuote` — transfer quote balance to an arbitrary recipient [5](#0-4) 
- `LiquidateSubaccount` — force-liquidate the victim's positions at a loss [6](#0-5) 
- `MintNlp` / `BurnNlp` — manipulate NLP positions [7](#0-6) 

The corrupted state is `linkedSigners[subaccount]`, which directly controls signing authority over all subaccount assets. The asset delta is the full collateral balance of the victim subaccount.

---

### Likelihood Explanation

Linked signers are explicitly designed for delegation to less-trusted parties such as trading bots, API keys, or third-party services. The `getLinkedSigner` function even propagates the linked signer from a parent subaccount to all its isolated subaccounts: [8](#0-7) 

Any of these delegated parties — if malicious or compromised — can execute the re-delegation silently in a single sequencer-submitted transaction. The victim has no on-chain mechanism to detect or prevent this before the attacker acts. The attack requires no privileged access beyond holding a currently-linked signer key.

---

### Recommendation

Change the `allowLinkedSigner` flag to `false` for the `LinkSigner` fast-mode transaction handler, so that only the subaccount owner's key can authorize a change to the linked signer:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // only the subaccount owner may re-link
);
```

This mirrors the slow-mode path, which already enforces `validateSender(txn.sender, sender)` to restrict `LinkSigner` to the subaccount owner.

---

### Proof of Concept

1. Alice owns subaccount `S` and links trading bot `B` as her linked signer: `linkedSigners[S] = B`.
2. Bot `B` (malicious or compromised) constructs a `SignedLinkSigner` transaction: `{sender: S, signer: attacker, nonce: N}`, signed by `B`'s key.
3. The sequencer includes this transaction in a batch via `submitTransactionsChecked`.
4. `processTransactionImpl` reaches the `LinkSigner` branch, calls `validateSignedTx(..., true)`.
5. `Verifier.validateSignature` accepts `B`'s signature because `recovered == linkedSigner`.
6. `linkedSigners[S]` is overwritten with `attacker`.
7. The attacker signs a `WithdrawCollateral` transaction for subaccount `S`, draining Alice's collateral to an external address. [1](#0-0) [2](#0-1)

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

**File:** core/contracts/EndpointTx.sol (L391-412)
```text
        if (txType == IEndpoint.TransactionType.LiquidateSubaccount) {
            IEndpoint.SignedLiquidateSubaccount memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedLiquidateSubaccount)
            );
            if (signedTx.tx.sender != N_ACCOUNT) {
                validateSignedTx(
                    signedTx.tx.sender,
                    signedTx.tx.nonce,
                    transaction,
                    signedTx.signature,
                    true
                );
                // No liquidation fee for finalization (productId == uint32.max) because:
                // 1) The liquidator receives no profit from finalization
                // 2) Finalization can only occur once per underwater subaccount, eliminating
                //    sybil attack concerns that would otherwise require a fee deterrent.
                if (signedTx.tx.productId != type(uint32).max) {
                    chargeFee(signedTx.tx.sender, LIQUIDATION_FEE);
                }
            }
            clearinghouse.liquidateSubaccount(signedTx.tx);
```

**File:** core/contracts/EndpointTx.sol (L418-436)
```text
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

**File:** core/contracts/EndpointTx.sol (L534-573)
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
            chargeFee(signedTx.tx.sender, HEALTHCHECK_FEE);
            priceX18[NLP_PRODUCT_ID] = signedTx.oraclePriceX18;
            clearinghouse.mintNlp(
                signedTx.tx,
                signedTx.oraclePriceX18,
                nlpPools,
                signedTx.nlpPoolRebalanceX18
            );
        } else if (txType == IEndpoint.TransactionType.BurnNlp) {
            IEndpoint.SignedBurnNlp memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedBurnNlp)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            chargeFee(signedTx.tx.sender, HEALTHCHECK_FEE);
            priceX18[NLP_PRODUCT_ID] = signedTx.oraclePriceX18;
            clearinghouse.burnNlp(
                signedTx.tx,
                signedTx.oraclePriceX18,
                nlpPools,
                signedTx.nlpPoolRebalanceX18
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
