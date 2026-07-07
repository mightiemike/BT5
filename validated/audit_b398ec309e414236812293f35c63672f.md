### Title
Linked Signer Can Override Itself in `linkedSigners` Mapping, Permanently Hijacking Subaccount Signing Authority — (`File: core/contracts/EndpointTx.sol`)

---

### Summary

The `LinkSigner` transaction handler in `EndpointTx.sol` validates the transaction with `allowLinkedSigner = true`, meaning the **currently registered linked signer** can sign a new `LinkSigner` transaction to replace itself with any attacker-controlled address. This is a direct analog to the PRBProxy plugin collision: a registered delegate can silently override the mapping entry that controls signing authority, without the subaccount owner's consent.

---

### Finding Description

In `EndpointTx.sol`, the fast-path `LinkSigner` handler at lines 576–590 calls `validateSignedTx` with `allowLinkedSigner = true`:

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

`validateSignedTx` internally calls `validateSignature`, which passes `getLinkedSigner(sender)` as the permitted signer when `allowLinkedSigner` is true:

```solidity
verifier.validateSignature(
    sender,
    allowLinkedSigner ? getLinkedSigner(sender) : address(0),
    digest,
    signature
);
``` [2](#0-1) 

`Verifier.validateSignature` accepts a signature from either the subaccount owner address or the linked signer:

```solidity
require(
    (recovered != address(0)) &&
        ((recovered == address(uint160(bytes20(sender)))) ||
            (recovered == linkedSigner)),
    ERR_INVALID_SIGNATURE
);
``` [3](#0-2) 

The `linkedSigners` mapping is a simple `bytes32 → address` map with no collision guard:

```solidity
mapping(bytes32 => address) internal linkedSigners;
``` [4](#0-3) 

The same `allowLinkedSigner = true` pattern also exists in the slow-mode path:

```solidity
} else if (txType == IEndpoint.TransactionType.LinkSigner) {
    ...
    validateSender(txn.sender, sender);
    requireSubaccount(txn.sender);
    linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
``` [5](#0-4) 

The slow-mode path uses `validateSender` (checks `msg.sender == address(uint160(bytes20(txSender)))`), which does **not** allow the linked signer to act. However, the fast-path (sequencer-submitted) `LinkSigner` at line 576 does allow it, and this is the primary attack surface since the sequencer path is the normal execution path.

---

### Impact Explanation

A malicious or compromised linked signer can:

1. Sign a `LinkSigner` transaction naming `signer = attacker_address` for the victim's subaccount.
2. The sequencer submits this transaction; `linkedSigners[victim_subaccount]` is overwritten with `attacker_address`.
3. The attacker now has full signing authority over the victim's subaccount.
4. The attacker can sign `WithdrawCollateral` / `WithdrawCollateralV2` to drain all collateral, `TransferQuote` to move quote balances, or `LiquidateSubaccount` to liquidate positions at will.

The linked signer is also inherited by all isolated subaccounts of the parent:

```solidity
return
    RiskHelper.isIsolatedSubaccount(subaccount)
        ? linkedSigners[
            IOffchainExchange(offchainExchange).getParentSubaccount(subaccount)
          ]
        : linkedSigners[subaccount];
``` [6](#0-5) 

So a single `LinkSigner` override propagates signing authority over all isolated subaccounts of the victim as well.

Additionally, even if the subaccount owner attempts to revoke the linked signer (by submitting a `LinkSigner` with `signer = address(0)`), the current linked signer can front-run this revocation in the sequencer queue by submitting its own `LinkSigner` override first, maintaining persistent access.

---

### Likelihood Explanation

Linked signers are typically hot wallets or automated trading bots — high-value targets for key compromise. Any user who has set a linked signer is exposed. The attack requires only a valid ECDSA signature from the current linked signer key, which is a realistic threat model for a DEX with programmatic trading. No admin access, governance capture, or sequencer compromise is required.

---

### Recommendation

`LinkSigner` transactions should only be authorized by the subaccount owner's key, not by the current linked signer. Change the `allowLinkedSigner` flag to `false` for the `LinkSigner` transaction type in the fast-path handler:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false  // linked signer must NOT be able to change the linked signer
);
```

This mirrors the principle that a delegate should not be able to modify its own delegation grant.

---

### Proof of Concept

1. Alice owns subaccount `alice_sub` and has set `linkedSigners[alice_sub] = bob_address`.
2. Attacker compromises Bob's key (or Bob is malicious).
3. Bob constructs a `LinkSigner` transaction: `{ sender: alice_sub, signer: attacker_address, nonce: current_nonce }`.
4. Bob signs this with his key and submits it to the sequencer.
5. The sequencer calls `processTransactionImpl` → `LinkSigner` branch → `validateSignedTx(..., true)`.
6. `validateSignature` recovers Bob's address, which equals `getLinkedSigner(alice_sub)` → passes.
7. `linkedSigners[alice_sub]` is overwritten with `attacker_address`.
8. Attacker signs a `WithdrawCollateralV2` transaction for `alice_sub` with `sendTo = attacker_wallet`.
9. All of Alice's collateral is drained. All isolated subaccounts of `alice_sub` are also under attacker control via the inherited linked signer lookup.

### Citations

**File:** core/contracts/EndpointTx.sol (L149-157)
```text
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

**File:** core/contracts/EndpointStorage.sol (L50-50)
```text
    mapping(bytes32 => address) internal linkedSigners;
```
