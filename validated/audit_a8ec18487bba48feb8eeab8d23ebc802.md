### Title
Linked Signer Can Self-Perpetuate Authorization by Front-Running Revocation via `LinkSigner` — (`File: core/contracts/EndpointTx.sol`)

---

### Summary

The `LinkSigner` transaction in the fast (sequencer) path is processed with `allowLinkedSigner = true`, meaning the **current linked signer** can produce a valid signature for a `LinkSigner` transaction that overwrites `linkedSigners[subaccount]` to any address they control. This is structurally identical to the PSP22 `approve` race condition: just as a PSP22 spender can front-run an allowance reduction to spend the old amount before the new one takes effect, a Nado linked signer can front-run a revocation by submitting a new `LinkSigner` that re-establishes their own access under a fresh address — consuming the owner's current nonce and invalidating the revocation.

---

### Finding Description

In `EndpointTx.processTransactionImpl`, the `LinkSigner` branch decodes a `SignedLinkSigner` and calls:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    true              // allowLinkedSigner = true
);
linkedSigners[signedTx.tx.sender] = address(
    uint160(bytes20(signedTx.tx.signer))
);
``` [1](#0-0) 

`validateSignedTx` with `allowLinkedSigner = true` calls `validateSignature`, which passes `getLinkedSigner(sender)` to the verifier:

```solidity
verifier.validateSignature(
    sender,
    allowLinkedSigner ? getLinkedSigner(sender) : address(0),
    digest,
    signature
);
``` [2](#0-1) 

The verifier accepts a signature from **either** the subaccount owner address **or** the linked signer address:

```solidity
require(
    (recovered != address(0)) &&
        ((recovered == address(uint160(bytes20(sender)))) ||
            (recovered == linkedSigner)),
    ERR_INVALID_SIGNATURE
);
``` [3](#0-2) 

Therefore, the current linked signer (`Bob`) can construct and sign a valid `LinkSigner` transaction for Alice's subaccount, setting `signedTx.tx.signer` to any address Bob controls (`Bob2`). The `linkedSigners` mapping is a plain overwrite with no ownership check beyond the signature:

```solidity
mapping(bytes32 => address) internal linkedSigners;
``` [4](#0-3) 

The nonce is keyed on the subaccount owner's address:

```solidity
require(
    nonce == nonces[address(uint160(bytes20(sender)))]++,
    ERR_WRONG_NONCE
);
``` [5](#0-4) 

This means Bob and Alice are racing to consume the **same nonce slot**. Whoever the sequencer processes first wins.

---

### Impact Explanation

Once Bob's `LinkSigner` (pointing to `Bob2`) is processed before Alice's revocation:

1. Alice's revocation transaction fails with `ERR_WRONG_NONCE` (nonce already consumed).
2. `Bob2` is now the linked signer and can sign any transaction that accepts `allowLinkedSigner = true`:
   - `WithdrawCollateral` — drain collateral to the default withdrawal address. [6](#0-5) 
   - `TransferQuote` — transfer quote-token balance to any registered subaccount. [7](#0-6) 
   - `BurnNlp` — burn NLP tokens from Alice's subaccount. [8](#0-7) 
   - `LiquidateSubaccount` — initiate liquidations on behalf of Alice's subaccount. [9](#0-8) 
3. Alice must sign a new `LinkSigner` at nonce `N+1`, but `Bob2` can again race with nonce `N+1` to link to `Bob3`, creating an indefinitely renewable access chain.

**Corrupted state**: `linkedSigners[aliceSubaccount]` is permanently controlled by the attacker. **Asset delta**: full collateral balance of Alice's subaccount is reachable via `WithdrawCollateral` or `TransferQuote`.

---

### Likelihood Explanation

The attack requires the sequencer to order Bob's `LinkSigner` before Alice's revocation. The sequencer is off-chain and processes transactions in submission order. A malicious linked signer who:
- monitors Alice's signed transactions (e.g., via a shared API key or compromised frontend), or
- simply submits their front-running `LinkSigner` immediately upon receiving any signal that Alice intends to revoke,

can reliably win the race. The sequencer has no obligation to prioritize the subaccount owner's transaction. This is **Medium** likelihood — it requires a malicious linked signer (an explicitly listed attacker role in scope) and sequencer submission timing, but no privileged access beyond the already-granted linked signer role.

---

### Recommendation

Remove `allowLinkedSigner = true` from the `LinkSigner` branch in `processTransactionImpl`. The linked signer should never be permitted to modify its own authorization. Change the call to:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // only the subaccount owner may change the linked signer
);
``` [10](#0-9) 

This mirrors the slow-mode `LinkSigner` path, which already enforces owner-only authorization via `validateSender(txn.sender, sender)`:

```solidity
validateSender(txn.sender, sender);
requireSubaccount(txn.sender);
linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
``` [11](#0-10) 

---

### Proof of Concept

**Setup**: Alice's subaccount `aliceSub` has linked signer `Bob`. Current nonce for Alice's address is `N`. Bob has full knowledge of Alice's intent to revoke (e.g., via a shared trading terminal).

**Step 1 — Alice signs revocation** (nonce `N`, `signer = bytes32(0)`):
```
LinkSigner { sender: aliceSub, signer: 0x000...000, nonce: N }
signed by Alice's private key
```

**Step 2 — Bob races** (nonce `N`, `signer = Bob2`):
```
LinkSigner { sender: aliceSub, signer: Bob2, nonce: N }
signed by Bob's private key  ← valid because allowLinkedSigner=true
```

**Step 3 — Sequencer processes Bob's transaction first**:
- `validateNonce(aliceSub, N)` passes; nonce incremented to `N+1`
- `validateSignature(aliceSub, Bob, digest, bobSig)` passes (Bob == linkedSigner)
- `linkedSigners[aliceSub] = Bob2`

**Step 4 — Alice's revocation arrives**:
- `validateNonce(aliceSub, N)` **reverts** — nonce is now `N+1`
- Alice's revocation is silently dropped

**Step 5 — Bob2 withdraws**:
```
WithdrawCollateral { sender: aliceSub, productId: 0, amount: fullBalance, nonce: N+1 }
signed by Bob2's private key  ← valid because allowLinkedSigner=true
```
- `clearinghouse.withdrawCollateral(aliceSub, ...)` executes, draining Alice's collateral. [6](#0-5)

### Citations

**File:** core/contracts/EndpointTx.sol (L73-76)
```text
        require(
            nonce == nonces[address(uint160(bytes20(sender)))]++,
            ERR_WRONG_NONCE
        );
```

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

**File:** core/contracts/EndpointTx.sol (L237-239)
```text
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

**File:** core/contracts/EndpointTx.sol (L554-573)
```text
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

**File:** core/contracts/Verifier.sol (L298-303)
```text
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
