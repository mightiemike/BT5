### Title
Linked Signer Authorization Persists Indefinitely with No Expiry, Enabling Permanent Subaccount Takeover via Re-Linking — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

The `linkedSigners` mapping stores delegated signing authority as a bare `address` with no expiry timestamp or block-number bound. A linked signer can authorize a new `LinkSigner` fast-mode transaction (because `allowLinkedSigner = true`), re-pointing the delegation to an attacker-controlled address. The legitimate owner's only on-chain recourse is a slow-mode revocation, which carries a hardcoded 3-day delay. During that window the attacker retains full signing authority and can drain all collateral.

---

### Finding Description

**Root cause — no expiry field in `LinkSigner` / `linkedSigners`**

`EndpointStorage.sol` declares:

```solidity
mapping(bytes32 => address) internal linkedSigners;
``` [1](#0-0) 

The `LinkSigner` struct contains only `sender`, `signer`, and `nonce` — no `expiry`, no `validUntil`, no block-number bound:

```solidity
struct LinkSigner {
    bytes32 sender;
    bytes32 signer;
    uint64 nonce;
}
``` [2](#0-1) 

Once set, the entry in `linkedSigners` persists forever until explicitly overwritten.

**Compounding factor — linked signer can re-link**

In `processTransactionImpl`, the fast-mode `LinkSigner` handler calls `validateSignedTx` with `allowLinkedSigner = true`:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    true          // ← linked signer is accepted
);
linkedSigners[signedTx.tx.sender] = address(
    uint160(bytes20(signedTx.tx.signer))
);
``` [3](#0-2) 

This means a compromised linked signer can submit a `LinkSigner` transaction to replace itself with a fresh attacker-controlled address, perpetuating the compromise.

**Slow-mode revocation has a 3-day delay**

The only path that enforces `msg.sender == subaccount owner` for `LinkSigner` is the slow-mode path (`validateSender` check at line 237). But slow-mode transactions are queued with a hardcoded `SLOW_MODE_TX_DELAY`:

```solidity
executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
``` [4](#0-3) 

During those 3 days the attacker's re-linked signer remains valid.

**What the linked signer can authorize**

`validateSignedTx` with `allowLinkedSigner = true` is used for:

| Transaction | Line |
|---|---|
| `WithdrawCollateral` | 418–424 |
| `WithdrawCollateralV2` (when `sendTo == address(0)`) | 442–448 |
| `LiquidateSubaccount` | 397–403 |
| `MintNlp` / `BurnNlp` | 539–565 |
| `TransferQuote` | 599–605 |
| `LinkSigner` (re-link) | 581–590 | [5](#0-4) [6](#0-5) 

---

### Impact Explanation

A compromised linked signer key gives an attacker:

1. Immediate ability to withdraw all collateral from the subaccount via `WithdrawCollateral`.
2. Ability to re-link to a new attacker-controlled address before the user can revoke, locking in the compromise for at least 3 days.
3. Ability to transfer quote assets to any registered subaccount via `TransferQuote`.
4. Ability to burn NLP tokens held by the subaccount.

The corrupted state is: `linkedSigners[victimSubaccount]` is overwritten to an attacker address; collateral balances in `SpotEngine` / `PerpEngine` are drained to zero.

---

### Likelihood Explanation

Linked signers are the standard mechanism for API-key-style trading on Nado. Any user who has ever linked a signer and whose linked-signer private key is later exposed (malware, shared workstation, leaked `.env`, compromised trading bot) faces this risk. Because the delegation never expires, keys that were used months ago and forgotten remain valid attack vectors indefinitely. This is a realistic, non-theoretical scenario for an active trading platform.

---

### Recommendation

1. **Add an expiry field to `LinkSigner`**: Extend the struct with `uint64 expiresAt` (block timestamp). `validateSignature` should reject a linked signer whose `expiresAt` has passed.
2. **Disallow linked signer from re-linking**: Change `allowLinkedSigner` to `false` for the fast-mode `LinkSigner` handler so only the subaccount owner can change the delegation.
3. **Emit an event on link/unlink** to allow users and monitoring systems to detect unauthorized re-linking.

---

### Proof of Concept

```
1. Alice links signer key K (address A) via LinkSigner fast-mode tx.
   → linkedSigners[aliceSubaccount] = A

2. Attacker obtains key K (e.g., from a compromised trading bot).

3. Attacker signs a new LinkSigner tx:
     sender = aliceSubaccount
     signer = attackerAddress B
     nonce  = current nonce N
   Submits to sequencer. Sequencer processes it.
   → validateSignedTx passes (linked signer A signs for aliceSubaccount, allowLinkedSigner=true)
   → linkedSigners[aliceSubaccount] = B

4. Alice notices and submits a slow-mode LinkSigner to revoke.
   → executableAt = now + 3 days

5. During the 3-day window, attacker uses key B to sign:
     WithdrawCollateral { sender=aliceSubaccount, productId=QUOTE, amount=MAX }
   → validateSignedTx passes (linked signer B, allowLinkedSigner=true)
   → clearinghouse.withdrawCollateral drains Alice's collateral.

6. Alice's slow-mode revocation executes after 3 days — account is empty.
``` [7](#0-6) [5](#0-4) [1](#0-0)

### Citations

**File:** core/contracts/EndpointStorage.sol (L50-50)
```text
    mapping(bytes32 => address) internal linkedSigners;
```

**File:** core/contracts/interfaces/IEndpoint.sol (L176-180)
```text
    struct LinkSigner {
        bytes32 sender;
        bytes32 signer;
        uint64 nonce;
    }
```

**File:** core/contracts/EndpointTx.sol (L377-377)
```text
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
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

**File:** core/contracts/EndpointTx.sol (L442-465)
```text
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
