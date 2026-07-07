### Title
Linked Signer Can Re-Link to Attacker-Controlled Address, Bypassing Subaccount Ownership Invariant — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

In `EndpointTx.processTransactionImpl`, the `LinkSigner` transaction is validated with `allowLinkedSigner = true`. This means the **current linked signer** can sign a `LinkSigner` transaction to replace itself with any arbitrary address — including an attacker-controlled one — without the subaccount owner's involvement. This is the Nado analog to the `approve` + `transferFrom` bypass: a delegated authority (linked signer) can mutate the delegation itself, circumventing the invariant that only the subaccount owner controls who holds signing authority.

---

### Finding Description

In `EndpointTx.processTransactionImpl`, the `LinkSigner` case:

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

`validateSignedTx` with `allowLinkedSigner = true` resolves the valid signer as either the subaccount owner **or** the current linked signer:

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

Because the linked signer is accepted as a valid signer for `LinkSigner`, it can write a new address into `linkedSigners[subaccount]`, permanently replacing itself with an attacker-controlled address.

The protocol is demonstrably aware of linked-signer risk: `transferQuote` explicitly comments *"require the sender address to be the same as the recipient address // otherwise linked signers can transfer out"* and enforces `bytes20(txn.sender) == bytes20(txn.recipient)`. [3](#0-2) 

Similarly, `WithdrawCollateralV2` restricts linked signers to `sendTo == address(0)` only:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    signedTx.tx.sendTo == address(0)   // allowLinkedSigner only when sendTo is zero
);
``` [4](#0-3) 

No equivalent guard exists for `LinkSigner`.

---

### Impact Explanation

Once the attacker holds the linked signer role, they can:

1. **Sign `MintNlp`** (also processed with `allowLinkedSigner = true`) to lock the victim's quote balance into NLP for the full `NLP_LOCK_PERIOD`, making those funds temporarily inaccessible. [5](#0-4) 

2. **Maintain persistent access** — the victim's only recovery path is a slow-mode `LinkSigner` submission, which carries a mandatory `SLOW_MODE_TX_DELAY` (hardcoded to three days). During this window, the attacker retains full signing authority and can continue to lock funds. [6](#0-5) 

3. **Sign `LiquidateSubaccount`** using the victim's subaccount as liquidator, incurring `LIQUIDATION_FEE` charges against the victim's balance on each call. [7](#0-6) 

**Broken invariant**: Only the subaccount owner should be able to change the linked signer. The current code allows the linked signer — a trading delegate — to mutate its own delegation, which is an unauthorized subaccount mutation.

---

### Likelihood Explanation

Linked signers are commonly automated trading bots or API keys. A leaked or compromised bot key is a realistic threat. The attacker needs only one valid signed `LinkSigner` transaction to be included by the sequencer, which processes all valid signed transactions without distinguishing intent. No privileged access, governance capture, or social engineering is required beyond the initial key compromise.

---

### Recommendation

Change the `LinkSigner` fast-path processing to `allowLinkedSigner = false`:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // only the subaccount owner may change the linked signer
);
```

This mirrors the existing pattern in `WithdrawCollateralV2` where linked signers are explicitly excluded from sensitive operations. The slow-mode `LinkSigner` path already enforces owner-only control via `validateSender`, so this change aligns the fast path with the slow path's security model. [6](#0-5) 

---

### Proof of Concept

1. Alice links Bob (an automated trading bot) as her linked signer via `LinkSigner`.
2. Mallory compromises Bob's private key.
3. Mallory constructs a `SignedLinkSigner` transaction with `tx.sender = Alice's subaccount`, `tx.signer = Mallory's address`, signed with Bob's key.
4. The sequencer includes the transaction; `validateSignedTx` accepts Bob's signature because `allowLinkedSigner = true` and Bob is the current linked signer.
5. `linkedSigners[Alice's subaccount]` is overwritten with Mallory's address.
6. Mallory signs a `MintNlp` transaction for Alice's subaccount, locking Alice's quote balance in NLP.
7. Alice submits a slow-mode `LinkSigner` to reset (3-day delay per `SLOW_MODE_TX_DELAY`).
8. During the 3-day window, Mallory re-signs `MintNlp` each time Alice's NLP unlocks, maintaining the lock.
9. Alice's funds remain inaccessible for the duration of the attack window. [1](#0-0) [8](#0-7)

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

**File:** core/contracts/EndpointTx.sol (L374-385)
```text
        IEndpoint.SlowModeConfig memory _slowModeConfig = slowModeConfig;
        requireUnsanctioned(sender);
        slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: transaction
        });
        // TODO: to save on costs we could potentially just emit something
        // for now, we can just create a separate loop in the engine that queries the remote
        // sequencer for slow mode transactions, and ignore the possibility of a reorgy attack
        slowModeConfig = _slowModeConfig;
    }
```

**File:** core/contracts/EndpointTx.sol (L396-411)
```text
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

**File:** core/contracts/EndpointTx.sol (L534-553)
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

**File:** core/contracts/Clearinghouse.sol (L220-225)
```text
        // require the sender address to be the same as the recipient address
        // otherwise linked signers can transfer out
        require(
            bytes20(txn.sender) == bytes20(txn.recipient),
            ERR_UNAUTHORIZED
        );
```
