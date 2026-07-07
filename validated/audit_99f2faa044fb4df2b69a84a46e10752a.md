### Title
Linked Signer Can Overwrite Its Own Authorization Context Without Subaccount Owner Consent — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

In `processTransactionImpl`, the `LinkSigner` transaction type is validated with `allowLinkedSigner = true`, meaning the **current linked signer** — not just the subaccount owner — can sign a `LinkSigner` transaction to replace itself with any attacker-controlled address. This allows a malicious or compromised linked signer to permanently inject a new signing authority into the `linkedSigners` context mapping, then exploit that injected authority to drain the victim's collateral via `TransferQuote`.

---

### Finding Description

The `linkedSigners` mapping in `EndpointStorage` is the persistent authorization context that governs who may sign sequencer-path transactions on behalf of a subaccount. It is the direct EVM analog to the `CpiContextAccount` in the reference vulnerability: it holds cross-invocation authorization state that spans multiple transactions.

In `processTransactionImpl` (`EndpointTx.sol`), the `LinkSigner` case is:

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

`validateSignedTx` with `allowLinkedSigner = true` resolves the permitted signer as either the subaccount owner **or** the current linked signer:

```solidity
verifier.validateSignature(
    sender,
    allowLinkedSigner ? getLinkedSigner(sender) : address(0),
    digest,
    signature
);
``` [2](#0-1) 

This means the current linked signer can sign a `LinkSigner` transaction to overwrite `linkedSigners[subaccount]` with any address — including an attacker-controlled one — without the subaccount owner's knowledge or consent.

This is **directly inconsistent** with the slow-mode path for the same transaction type, which correctly restricts `LinkSigner` to the subaccount owner via `validateSender`:

```solidity
} else if (txType == IEndpoint.TransactionType.LinkSigner) {
    IEndpoint.LinkSigner memory txn = abi.decode(
        transaction[1:],
        (IEndpoint.LinkSigner)
    );
    validateSender(txn.sender, sender);   // ← owner-only check
    requireSubaccount(txn.sender);
    linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
``` [3](#0-2) 

`validateSender` enforces that `address(uint160(bytes20(txn.sender))) == msg.sender`, i.e., only the address that owns the subaccount may change its linked signer in the slow-mode path. [4](#0-3) 

Once the attacker's address is injected into `linkedSigners`, it can immediately sign `TransferQuote` transactions, which also use `allowLinkedSigner = true`:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    true                          // ← allowLinkedSigner = true
);
...
clearinghouse.transferQuote(signedTx.tx);
``` [5](#0-4) 

`TransferQuote` moves quote-token balance from the victim's subaccount to any registered recipient subaccount, including one the attacker controls.

---

### Impact Explanation

A malicious or compromised linked signer can:

1. Sign a sequencer-path `LinkSigner` transaction replacing `linkedSigners[victim_subaccount]` with an attacker-controlled address.
2. The attacker's address now holds full signing authority over the victim's subaccount.
3. The attacker signs a `TransferQuote` transaction moving the victim's entire quote balance to an attacker-owned subaccount.
4. The attacker withdraws from their own subaccount via normal withdrawal flows.

The victim's only recovery path is a slow-mode `LinkSigner` to revoke the injected signer, but this is subject to the `SLOW_MODE_TX_DELAY` (hardcoded to three days), during which the attacker can drain all transferable collateral. [6](#0-5) 

---

### Likelihood Explanation

**Medium.** The attack requires the current linked signer to be malicious or compromised. Linked signers are explicitly hot wallets (API keys, automated trading bots) and are materially more likely to be compromised than the cold-wallet subaccount owner. The exploit requires no privileged protocol access, no admin keys, and no governance capture — only possession of the linked signer's private key, which is the exact threat model linked signers are exposed to.

---

### Recommendation

Change `allowLinkedSigner` to `false` for the `LinkSigner` case in `processTransactionImpl`, consistent with the slow-mode path. Only the subaccount owner (the address whose first 20 bytes match the subaccount) should be permitted to modify the `linkedSigners` authorization context:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // only subaccount owner may change linked signer
);
``` [7](#0-6) 

---

### Proof of Concept

1. Alice owns subaccount `alice_subaccount` and has set Bob (`bob_address`) as her linked signer via a prior `LinkSigner` transaction. `linkedSigners[alice_subaccount] = bob_address`.
2. Bob (malicious) constructs a sequencer-path `SignedLinkSigner` transaction: `sender = alice_subaccount`, `signer = attacker_address`, signed with `bob_address`'s private key.
3. The sequencer submits this to `processTransactionImpl`. `validateSignedTx` accepts Bob's signature because `allowLinkedSigner = true` and `getLinkedSigner(alice_subaccount) == bob_address`.
4. `linkedSigners[alice_subaccount]` is overwritten with `attacker_address`.
5. The attacker constructs a `SignedTransferQuote` transaction: `sender = alice_subaccount`, `recipient = attacker_subaccount`, `amount = alice_full_balance`, signed with `attacker_address`'s private key.
6. `validateSignedTx` accepts the attacker's signature because `allowLinkedSigner = true` and `getLinkedSigner(alice_subaccount) == attacker_address`.
7. `clearinghouse.transferQuote` moves Alice's entire quote balance to `attacker_subaccount`.
8. The attacker withdraws from `attacker_subaccount`. Alice's collateral is fully drained. [8](#0-7) [9](#0-8)

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

**File:** core/contracts/EndpointTx.sol (L594-614)
```text
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

**File:** core/contracts/Endpoint.sol (L151-166)
```text

        slowModeTxs[_slowModeConfig.txCount++] = SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: abi.encodePacked(
                uint8(TransactionType.DepositCollateral),
                abi.encode(
                    DepositCollateral({
                        sender: subaccount,
                        productId: productId,
                        amount: amount
                    })
                )
            )
        });
        slowModeConfig = _slowModeConfig;
```

**File:** core/contracts/EndpointStorage.sol (L50-50)
```text
    mapping(bytes32 => address) internal linkedSigners;
```
