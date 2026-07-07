### Title
Malicious Linked Signer Can Perpetuate Own Authorization and Defeat Slow-Mode Revocation — (`core/contracts/EndpointTx.sol`)

---

### Summary

The fast-mode `LinkSigner` path accepts signatures from the **current linked signer** (`allowLinkedSigner=true`). A malicious linked signer can exploit this to counter a victim's slow-mode revocation attempt: by submitting a fast-mode `LinkSigner(signer=attacker)` signed with their own key, the attacker re-asserts themselves before the slow-mode revocation executes, then drains the account during the 3-day window.

---

### Finding Description

**Fast-mode `LinkSigner` path** in `processTransactionImpl`:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    true          // ← allowLinkedSigner = true
);
linkedSigners[signedTx.tx.sender] = address(uint160(bytes20(signedTx.tx.signer)));
``` [1](#0-0) 

`validateSignedTx` with `allowLinkedSigner=true` resolves to `validateSignature`, which accepts a signature from either the account owner **or** `linkedSigners[sender]`:

```solidity
verifier.validateSignature(
    sender,
    allowLinkedSigner ? getLinkedSigner(sender) : address(0),
    digest,
    signature
);
``` [2](#0-1) 

```solidity
address recovered = ECDSA.recover(digest, signature);
require(
    (recovered != address(0)) &&
        ((recovered == address(uint160(bytes20(sender)))) ||
            (recovered == linkedSigner)),
    ERR_INVALID_SIGNATURE
);
``` [3](#0-2) 

**Slow-mode `LinkSigner` path** in `processSlowModeTransactionImpl`:

```solidity
validateSender(txn.sender, sender);   // only checks msg.sender == subaccount owner
requireSubaccount(txn.sender);
linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
``` [4](#0-3) 

`validateSender` only checks that `address(bytes20(txSender)) == sender` — **no nonce is consumed**:

```solidity
function validateSender(bytes32 txSender, address sender) internal view {
    require(
        address(uint160(bytes20(txSender))) == sender ||
            sender == address(this),
        ERR_SLOW_MODE_WRONG_SENDER
    );
}
``` [5](#0-4) 

The slow-mode delay is hardcoded to 3 days:

```solidity
executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
``` [6](#0-5) 

```solidity
uint64 constant SLOW_MODE_TX_DELAY = 3 * 24 * 60 * 60; // 3 days
``` [7](#0-6) 

---

### Impact Explanation

A malicious linked signer can:
1. Observe the victim's slow-mode `LinkSigner` revocation in the queue
2. Immediately submit a fast-mode `LinkSigner(signer=attacker)` **signed with their own key** (valid because `allowLinkedSigner=true`)
3. The sequencer processes the fast-mode tx immediately — `linkedSigners[victim] = attacker` (nonce N consumed)
4. The attacker signs fast-mode `WithdrawCollateral` transactions (also `allowLinkedSigner=true`) to drain the account
5. After 3 days the slow-mode tx executes and sets `linkedSigners[victim] = legitimate`, but funds are already gone

`WithdrawCollateral` fast-mode also uses `allowLinkedSigner=true`, confirming the drain path:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    true   // ← linked signer can withdraw
);
``` [8](#0-7) 

The broken invariant: **the most recently authorized signer change (slow-mode, submitted first by the account owner) is not the effective one** — the attacker's fast-mode override wins in the short term, and the slow-mode override arrives too late.

---

### Likelihood Explanation

- **Prerequisite**: The attacker must already be the current linked signer. This is a realistic scenario — users legitimately delegate linked signers for trading automation, then later attempt to revoke them.
- **No privileged access required**: The attacker uses only their own private key (as the current linked signer) and the public `submitSlowModeTransaction` / sequencer fast-mode submission interfaces.
- **Observable trigger**: The slow-mode queue is on-chain; the attacker can watch for a revocation tx targeting their address and react immediately.
- **Sequencer ordering**: Fast-mode transactions are processed by the sequencer before slow-mode transactions by design, making the ordering deterministic and exploitable.

---

### Recommendation

1. **Restrict `allowLinkedSigner` for `LinkSigner` fast-mode**: Change the flag to `false` so that only the account owner's key can authorize a linked signer change. A linked signer should not be able to re-delegate or perpetuate their own authorization.
2. **Alternatively, add a nonce check to slow-mode `LinkSigner`**: Require the slow-mode `LinkSigner` to include and validate a nonce, so a fast-mode tx that increments the nonce invalidates any pending slow-mode tx for the same account.
3. **Consider a time-lock or cancellation mechanism**: Allow the account owner to cancel a pending slow-mode tx, or enforce that a fast-mode `LinkSigner` cancels any pending slow-mode `LinkSigner` for the same sender.

---

### Proof of Concept

```solidity
// Setup: victim has previously set attacker as linked signer
linkedSigners[victimSubaccount] = attacker;

// t=0: victim submits slow-mode LinkSigner to revoke attacker
endpoint.submitSlowModeTransaction(
    abi.encodePacked(
        uint8(TransactionType.LinkSigner),
        abi.encode(LinkSigner({ sender: victimSubaccount, signer: bytes32(bytes20(legitimateAddress)) }))
    )
); // queued, executableAt = block.timestamp + 3 days

// t=1: attacker (as current linked signer) submits fast-mode LinkSigner(signer=attacker)
// signed with attacker's own key — valid because allowLinkedSigner=true
sequencer.processTransaction(
    SignedLinkSigner({
        tx: LinkSigner({ sender: victimSubaccount, signer: bytes32(bytes20(attacker)), nonce: N }),
        signature: attackerSignature   // attacker signs with their own key
    })
);

// assert: linkedSigners[victimSubaccount] == attacker
// attacker now signs WithdrawCollateral to drain the account

// warp +3 days
vm.warp(block.timestamp + 3 days + 1);
endpoint.executeSlowModeTransaction();

// assert: linkedSigners[victimSubaccount] == legitimateAddress
// but account balance == 0 (already drained)
```

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

**File:** core/contracts/EndpointTx.sol (L377-377)
```text
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
```

**File:** core/contracts/EndpointTx.sol (L418-424)
```text
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
```

**File:** core/contracts/EndpointTx.sol (L581-590)
```text
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

**File:** core/contracts/common/Constants.sol (L50-50)
```text
uint64 constant SLOW_MODE_TX_DELAY = 3 * 24 * 60 * 60; // 3 days
```
