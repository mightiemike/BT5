### Title
Persistent `linkedSigners` Authorization With No Expiry Allows Compromised Linked Signer to Perpetuate Subaccount Control and Drain Collateral — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

The `linkedSigners` mapping in `EndpointStorage.sol` stores a permanent, non-expiring authorization granting a linked signer full signing authority over a subaccount. Because `LinkSigner` transactions themselves accept linked signer signatures (`allowLinkedSigner = true`), a compromised linked signer can re-link to a new attacker-controlled address before the owner can revoke via the 3-day slow mode path. This permanently locks the owner out and enables collateral theft via `TransferQuote`.

---

### Finding Description

`EndpointStorage.sol` declares the `linkedSigners` mapping as a persistent, unbounded authorization: [1](#0-0) 

Once set, this mapping has no expiry, no automatic revocation, and no time-bound scope. The `LinkSigner` transaction type is processed in `processTransactionImpl` with `allowLinkedSigner = true`: [2](#0-1) 

This means the **current linked signer** can sign a new `LinkSigner` transaction to replace itself with any address — including an attacker-controlled one. The owner's only on-chain revocation path is the slow mode queue, which enforces a hardcoded 3-day delay: [3](#0-2) 

During those 3 days, the compromised linked signer can continuously re-link to a new address, making revocation impossible. The slow mode `LinkSigner` path does validate `msg.sender` against the subaccount owner address: [4](#0-3) 

But this is irrelevant because the attacker operates through the fast (sequencer-processed) path, where the linked signer's signature is sufficient.

Once the attacker controls the linked signer slot, they can drain the subaccount via `TransferQuote`, which also accepts linked signer signatures (`allowLinkedSigner = true`): [5](#0-4) 

The `TransferQuote` path transfers quote assets to any registered subaccount — including one the attacker controls — with no restriction on the recipient.

The `getLinkedSigner` function confirms that the linked signer is resolved from the persistent mapping at execution time with no staleness check: [6](#0-5) 

---

### Impact Explanation

A compromised linked signer can:

1. Sign a `LinkSigner` transaction pointing to a new attacker-controlled address, preventing the owner from revoking.
2. Sign `TransferQuote` to move all quote collateral to an attacker-controlled subaccount.
3. Sign `WithdrawCollateral` (V1) to initiate withdrawals to the default address (subaccount owner), but combined with step 2, the quote balance is already drained.
4. Repeat step 1 indefinitely to stay ahead of any slow mode revocation attempt.

The corrupted state is `linkedSigners[victimSubaccount]` and the spot balance of the victim subaccount. The impact is complete loss of quote collateral for any subaccount that has ever set a linked signer.

---

### Likelihood Explanation

Linked signers are a core protocol feature used by trading bots, automated strategies, and NLP pool operators. Any of these use cases involves a hot key with persistent, unbounded authority. Key compromise of a hot wallet is a realistic and common event. Once compromised, the attack is deterministic and requires no further preconditions — the attacker simply submits valid signed transactions to the sequencer, which has no basis to reject them.

---

### Recommendation

1. **Add an expiry field to `LinkSigner`**: Include a `uint64 expiresAt` in the `LinkSigner` struct and enforce it in `getLinkedSigner`. Expired linked signers should be treated as `address(0)`.
2. **Disallow linked signers from signing `LinkSigner` transactions**: Change the `allowLinkedSigner` flag to `false` for `LinkSigner` processing in `processTransactionImpl`. Only the subaccount owner (primary key) should be able to change the linked signer.
3. **Provide a fast revocation path**: Allow the subaccount owner to revoke a linked signer via a direct on-chain call (not gated by the 3-day slow mode delay), since revocation is a security-critical operation.

---

### Proof of Concept

1. Alice sets linked signer to hot-wallet address `B` via a sequencer-processed `LinkSigner` transaction. `linkedSigners[aliceSubaccount] = B`.
2. Attacker compromises `B`'s private key.
3. Attacker signs a `LinkSigner` transaction: `{sender: aliceSubaccount, signer: attackerAddress, nonce: N}` using `B`'s key. This is valid because `allowLinkedSigner = true` at `EndpointTx.sol:586`.
4. Sequencer processes the transaction. `linkedSigners[aliceSubaccount] = attackerAddress`.
5. Alice submits a slow mode `LinkSigner` to revoke. It is queued with a 3-day delay (`EndpointTx.sol:377`).
6. Before the 3 days elapse, attacker signs `TransferQuote`: `{sender: aliceSubaccount, recipient: attackerSubaccount, amount: fullBalance, nonce: N+1}` using `attackerAddress`. Sequencer processes it. Alice's quote balance is transferred to the attacker.
7. Attacker signs another `LinkSigner` to a new address `D`, invalidating Alice's pending revocation. Alice's slow mode transaction executes but sets `linkedSigners[aliceSubaccount] = address(0)` — the attacker has already drained the account and moved on. [2](#0-1) [5](#0-4) [1](#0-0)

### Citations

**File:** core/contracts/EndpointStorage.sol (L50-50)
```text
    mapping(bytes32 => address) internal linkedSigners;
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

**File:** core/contracts/EndpointTx.sol (L376-380)
```text
        slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: transaction
        });
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
