### Title
Linked Signer Can Unilaterally Replace Itself to Achieve Full Account Takeover — (`core/contracts/EndpointTx.sol`)

---

### Summary

The `LinkSigner` sequencer transaction is validated with `allowLinkedSigner = true`, meaning the **current linked signer** can sign a `LinkSigner` transaction to replace itself with any arbitrary address. Because all high-value transactions (`WithdrawCollateral`, `TransferQuote`, `LiquidateSubaccount`, `MintNlp`, `BurnNlp`) also accept linked-signer signatures, a malicious or compromised linked signer can silently rotate authority to an attacker-controlled key and subsequently drain the subaccount.

---

### Finding Description

In `processTransactionImpl`, the `LinkSigner` branch is:

```solidity
// EndpointTx.sol:576-590
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
        true          // ← allowLinkedSigner = true
    );
    linkedSigners[signedTx.tx.sender] = address(
        uint160(bytes20(signedTx.tx.signer))
    );
```

`validateSignedTx(..., true)` calls `validateSignature(..., allowLinkedSigner ? getLinkedSigner(sender) : address(0), ...)`, which in turn calls `verifier.validateSignature`:

```solidity
// Verifier.sol:297-303
address recovered = ECDSA.recover(digest, signature);
require(
    (recovered != address(0)) &&
        ((recovered == address(uint160(bytes20(sender)))) ||
            (recovered == linkedSigner)),   // ← linked signer accepted
    ERR_INVALID_SIGNATURE
);
```

The current linked signer is therefore a fully valid signer for a `LinkSigner` transaction. There is no restriction preventing it from setting `signedTx.tx.signer` to an arbitrary attacker-controlled address.

The `linkedSigners` mapping is the sole runtime authority gate for all sequencer transactions that pass `allowLinkedSigner = true`:

| Transaction type | `allowLinkedSigner` |
|---|---|
| `WithdrawCollateral` | `true` |
| `WithdrawCollateralV2` (sendTo == 0) | `true` |
| `TransferQuote` | `true` |
| `LiquidateSubaccount` | `true` |
| `MintNlp` / `BurnNlp` | `true` |
| **`LinkSigner`** | **`true`** |

This is the direct analog to the Llama finding: just as `authorizedScripts` could be mutated by a separate action with a different signer set after an action was already approved, `linkedSigners[subaccount]` can be mutated by the current linked signer — a different principal than the account owner — silently changing the execution authority for all future transactions against that subaccount.

---

### Impact Explanation

Once the linked signer rotates authority to attacker-controlled key LS2:

- LS2 signs a `WithdrawCollateral` (or `WithdrawCollateralV2` with `sendTo == address(0)`) transaction directing all collateral to an attacker wallet.
- LS2 signs a `TransferQuote` transaction to move quote balance to an attacker subaccount.
- LS2 can sign `LiquidateSubaccount` to force-liquidate the victim at a disadvantageous price.

The account owner has no on-chain mechanism to detect or block this before the sequencer processes the batch. The corrupted state is `linkedSigners[subaccount]`, and the asset delta is the full collateral balance of the subaccount.

---

### Likelihood Explanation

Linked signers are the standard mechanism for trading bots, automated market makers, and NLP pool operators. Any of the following realistic scenarios triggers the bug:

1. A trading-bot key is compromised (e.g., leaked API key, server breach).
2. A third-party trading service that was granted linked-signer authority turns malicious.
3. An NLP pool operator (`nlpSigners`) — set via `addNlpPool` / `updateNlpPool` — abuses the same path.

The attack requires no admin access, no governance capture, and no sequencer compromise. It is executable by any entity that currently holds a valid linked-signer key for a target subaccount.

---

### Recommendation

`LinkSigner` transactions must only be authorizable by the **subaccount owner** (the address embedded in the first 20 bytes of the `bytes32` subaccount identifier), never by the current linked signer. Change the `allowLinkedSigner` flag for the `LinkSigner` branch to `false`:

```solidity
// EndpointTx.sol — LinkSigner branch
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // ← only the subaccount owner may rotate the linked signer
);
```

This mirrors the fix applied in Llama (commit `e2c9ed`): the execution-affecting state (`authorizedScripts` / `linkedSigners`) must be locked to the principal who originally approved the action, not to a delegated party that could have a different (or adversarial) interest.

---

### Proof of Concept

1. **Setup**: Victim subaccount `V` has linked signer `LS1` (a trading bot). `V` holds 10,000 USDC collateral.

2. **Attack step 1 — rotate authority**: `LS1` constructs and signs a `LinkSigner` sequencer transaction:
   ```
   sender  = V
   signer  = LS2   // attacker-controlled address
   nonce   = nonces[address(V)]
   ```
   The sequencer accepts this because `validateSignedTx(..., true)` passes — `LS1` is the current linked signer. [1](#0-0) 

3. **Attack step 2 — drain funds**: `LS2` signs a `WithdrawCollateral` transaction:
   ```
   sender    = V
   productId = USDC_PRODUCT_ID
   amount    = 10_000e18
   ```
   `validateSignedTx(..., true)` resolves `getLinkedSigner(V)` → `LS2`, and `verifier.validateSignature` accepts `LS2`'s signature. [2](#0-1) 

4. **Result**: `clearinghouse.withdrawCollateral` transfers 10,000 USDC to the attacker. The victim's balance is zero. [3](#0-2) [4](#0-3)

### Citations

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

**File:** core/contracts/EndpointStorage.sol (L50-50)
```text
    mapping(bytes32 => address) internal linkedSigners;
```
