### Title
Linked Signer Can Self-Replace via `LinkSigner` with `allowLinkedSigner = true`, Enabling Permanent Subaccount Takeover - (`core/contracts/EndpointTx.sol`)

---

### Summary

In `EndpointTx.processTransactionImpl`, the `LinkSigner` transaction type is validated with `allowLinkedSigner = true`. This means the **current linked signer** can sign a `LinkSigner` transaction to replace itself with any attacker-controlled address — without the subaccount owner's consent. Once the attacker controls the linked signer, they can sign orders and sensitive transactions on behalf of the victim's subaccount, draining it through the sequencer's off-chain order matching.

---

### Finding Description

In `processTransactionImpl`, the `LinkSigner` branch is:

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
        true   // ← allowLinkedSigner = true
    );
    linkedSigners[signedTx.tx.sender] = address(
        uint160(bytes20(signedTx.tx.signer))
    );
}
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

And `Verifier.validateSignature` accepts either:

```solidity
require(
    (recovered != address(0)) &&
        ((recovered == address(uint160(bytes20(sender)))) ||
            (recovered == linkedSigner)),
    ERR_INVALID_SIGNATURE
);
``` [3](#0-2) 

This means the **linked signer itself** can sign a `LinkSigner` transaction to overwrite `linkedSigners[subaccount]` with the attacker's address. There is no timelock, no owner-only guard, and no two-step confirmation.

Once the attacker controls the linked signer, they can sign any transaction that accepts `allowLinkedSigner = true`, including:

- **`WithdrawCollateral` (V1)** — linked signer accepted, funds sent to `address(uint160(bytes20(sender)))` (the owner's address). [4](#0-3) 
- **`WithdrawCollateralV2` with `sendTo == address(0)`** — linked signer accepted. [5](#0-4) 
- **`MatchOrders`** — the linked signer is passed as `takerLinkedSigner`/`makerLinkedSigner` to `OffchainExchange.matchOrders`, where `_checkSignature` accepts it as a valid signer for order execution. [6](#0-5) 
- **`MintNlp` / `BurnNlp`** — linked signer accepted, enabling fee-draining burns. [7](#0-6) 
- **`LiquidateSubaccount`** — linked signer accepted, enabling the attacker to initiate liquidations from the victim's account, paying liquidation fees. [8](#0-7) 

The most critical path is **order signing**: the attacker can submit adversarial orders to the sequencer's off-chain book signed by the attacker-controlled linked signer. The sequencer, unaware of the compromise, will match them via `MatchOrders`, draining the victim's subaccount through unfavorable trades.

Additionally, the attacker can **continuously re-replace** the linked signer to prevent the owner from revoking it, since the owner and the attacker-controlled linked signer are in a race condition — both can sign `LinkSigner` transactions.

---

### Impact Explanation

A user whose linked signer key is compromised loses permanent control of their subaccount's signing authority. The attacker can:

1. Immediately replace the linked signer with their own address (single transaction, no delay).
2. Sign orders on behalf of the victim's subaccount, draining it through the sequencer's order matching.
3. Prevent the owner from revoking the linked signer by continuously re-replacing it.
4. Drain the account indirectly via `BurnNlp` fees and liquidation fees.

The corrupted state is `linkedSigners[subaccount]` in `EndpointStorage`, which governs signing authority for all sensitive operations on that subaccount. [9](#0-8) 

---

### Likelihood Explanation

Linked signers are explicitly designed as **hot wallets** for high-frequency trading — they are far more likely to be exposed than cold-wallet subaccount owners. Compromise vectors include: malicious frontend injection, phishing, or server-side key leakage in automated trading bots. The attack requires only a single signed transaction from the compromised key and takes effect immediately with no on-chain delay.

---

### Recommendation

Change the `LinkSigner` fast-path to use `allowLinkedSigner = false`, requiring the subaccount owner's signature (not the current linked signer's) to change the linked signer:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // owner must sign; linked signer cannot replace itself
);
``` [10](#0-9) 

This mirrors the mitigation recommended in the HomeFi report: use `msg.sender` (the real owner) instead of `_msgSender()` (which could be the forwarder/linked signer) for privileged role-change operations.

Optionally, introduce a timelock on linked signer replacement so the owner has a window to detect and cancel a malicious replacement before it takes effect.

---

### Proof of Concept

```
1. Alice sets linkedSigners[alice_subaccount] = hot_wallet_address
   (via a legitimate LinkSigner transaction signed by Alice)

2. Attacker compromises hot_wallet_address private key.

3. Attacker constructs a LinkSigner transaction:
     sender = alice_subaccount
     signer = attacker_address
     nonce  = current nonce for alice_subaccount

4. Attacker signs this transaction with hot_wallet_address (the current linked signer).
   validateSignedTx(..., allowLinkedSigner=true) accepts it because:
     recovered == linkedSigner (hot_wallet_address) ✓

5. linkedSigners[alice_subaccount] = attacker_address

6. Attacker submits adversarial orders to the sequencer's off-chain book,
   signed by attacker_address (now the valid linked signer).
   Sequencer calls MatchOrders → OffchainExchange._checkSignature:
     signer == linkedSigner (attacker_address) ✓
   Orders execute, draining alice_subaccount.

7. If Alice tries to revoke by signing a new LinkSigner,
   attacker immediately re-signs another LinkSigner to re-replace it,
   winning the race since the attacker monitors the mempool.
```

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

**File:** core/contracts/EndpointTx.sol (L396-412)
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

**File:** core/contracts/EndpointTx.sol (L503-514)
```text
            IEndpoint.MatchOrdersWithSigner memory txnWithSigner = IEndpoint
                .MatchOrdersWithSigner({
                    matchOrders: txn,
                    takerLinkedSigner: getLinkedSignerOrNlpSigner(
                        txn.taker.order.sender
                    ),
                    makerLinkedSigner: getLinkedSignerOrNlpSigner(
                        txn.maker.order.sender
                    ),
                    takerAmountDelta: 0
                });
            IOffchainExchange(offchainExchange).matchOrders(txnWithSigner);
```

**File:** core/contracts/EndpointTx.sol (L539-545)
```text
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
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

**File:** core/contracts/Verifier.sol (L296-304)
```text
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
