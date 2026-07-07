### Title
No Slippage or Deadline Protection for NLP Mint/Burn Operations — (`core/contracts/Clearinghouse.sol`, `core/contracts/interfaces/IEndpoint.sol`)

---

### Summary

The `MintNlp` and `BurnNlp` transaction types allow users to exchange quote tokens for NLP tokens (and vice versa) at a sequencer-supplied `oraclePriceX18`. Neither the user-signed message nor the on-chain execution enforces a minimum output amount or a deadline. A user who signs a `MintNlp` or `BurnNlp` transaction has no on-chain guarantee about the exchange rate at which their transaction will be settled.

---

### Finding Description

The `MintNlp` and `BurnNlp` structs that users sign contain only `sender`, `quoteAmount`/`nlpAmount`, and `nonce`: [1](#0-0) 

The `oraclePriceX18` used to compute the actual output is part of `SignedMintNlp`/`SignedBurnNlp` but is **not** included in the user's EIP-712 digest. The `Verifier.sol` digest construction confirms this: [2](#0-1) 

The digest for `MintNlp` covers only `sender`, `quoteAmount`, `nonce`. The digest for `BurnNlp` covers only `sender`, `nlpAmount`, `nonce`. The `oraclePriceX18` is entirely sequencer-supplied and unconstrained by the user's signature.

In `Clearinghouse.mintNlp()`, the output NLP amount is computed as:

```
nlpAmount = quoteAmount / oraclePriceX18
``` [3](#0-2) 

In `Clearinghouse.burnNlp()`, the output quote amount is computed as:

```
quoteAmount = nlpAmount * oraclePriceX18
``` [4](#0-3) 

There is no check in either function that the computed output meets any user-specified minimum. There is also no deadline field in the signed structs, so a signed transaction can be executed arbitrarily late.

---

### Impact Explanation

- **`MintNlp`**: A user signs to spend a fixed `quoteAmount`. If the NLP oracle price rises before execution, they receive fewer NLP tokens than anticipated, with no on-chain recourse.
- **`BurnNlp`**: A user signs to burn a fixed `nlpAmount`. If the NLP oracle price falls before execution, they receive fewer quote tokens than anticipated, with no on-chain recourse.

The magnitude of loss scales directly with the price movement and the transaction size. For large NLP positions, even a modest price move (e.g., 5–10%) translates to a material loss of quote tokens.

---

### Likelihood Explanation

The NLP price (`oraclePriceX18`) is updated by the sequencer via `UpdatePrice` transactions and reflects the aggregate value of the NLP pool. As the pool's composition changes (due to trading activity, funding payments, PnL settlement), the NLP price can move between the time a user signs a `MintNlp`/`BurnNlp` and the time the sequencer processes it. This is a normal operational condition, not an edge case requiring adversarial action.

---

### Recommendation

Add `minOutAmount` and `expiration` (deadline) fields to the `MintNlp` and `BurnNlp` structs, include them in the EIP-712 digest, and enforce them in `Clearinghouse.mintNlp()` and `Clearinghouse.burnNlp()`:

```solidity
struct MintNlp {
    bytes32 sender;
    uint128 quoteAmount;
    uint128 minNlpAmount;   // minimum NLP tokens to receive
    uint64  expiration;     // deadline timestamp
    uint64  nonce;
}

struct BurnNlp {
    bytes32 sender;
    uint128 nlpAmount;
    uint128 minQuoteAmount; // minimum quote tokens to receive
    uint64  expiration;     // deadline timestamp
    uint64  nonce;
}
```

In `mintNlp()`:
```solidity
require(block.timestamp <= txn.expiration, ERR_INVALID_TIME);
require(nlpAmount >= txn.minNlpAmount, ERR_SLIPPAGE_TOO_HIGH);
```

In `burnNlp()`:
```solidity
require(block.timestamp <= txn.expiration, ERR_INVALID_TIME);
require(quoteAmount >= txn.minQuoteAmount, ERR_SLIPPAGE_TOO_HIGH);
```

Note that `ERR_SLIPPAGE_TOO_HIGH` and `ERR_INVALID_TIME` already exist in the error constants file, indicating the protocol anticipated these checks. [5](#0-4) 

---

### Proof of Concept

1. NLP oracle price is currently $1.00. User signs `MintNlp { quoteAmount: 10_000e18, nonce: 5 }`, expecting ~10,000 NLP tokens.
2. Before the sequencer batches this transaction, significant trading activity causes the NLP pool value to increase; the sequencer updates the NLP price to $1.10.
3. The sequencer submits the `SignedMintNlp` with `oraclePriceX18 = 1.10e18`.
4. `Clearinghouse.mintNlp()` computes `nlpAmount = 10_000e18 / 1.10e18 ≈ 9_090e18`.
5. The user receives ~9,090 NLP tokens instead of ~10,000 — a ~9% shortfall — with no on-chain protection.

The same scenario applies in reverse for `BurnNlp` when the NLP price drops before execution. [6](#0-5) [7](#0-6)

### Citations

**File:** core/contracts/interfaces/IEndpoint.sol (L112-136)
```text
    struct MintNlp {
        bytes32 sender;
        uint128 quoteAmount;
        uint64 nonce;
    }

    struct SignedMintNlp {
        MintNlp tx;
        bytes signature;
        int128 oraclePriceX18;
        int128[] nlpPoolRebalanceX18;
    }

    struct BurnNlp {
        bytes32 sender;
        uint128 nlpAmount;
        uint64 nonce;
    }

    struct SignedBurnNlp {
        BurnNlp tx;
        bytes signature;
        int128 oraclePriceX18;
        int128[] nlpPoolRebalanceX18;
    }
```

**File:** core/contracts/Verifier.sol (L373-398)
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

**File:** core/contracts/Clearinghouse.sol (L453-483)
```text
    function mintNlp(
        IEndpoint.MintNlp calldata txn,
        int128 oraclePriceX18,
        IEndpoint.NlpPool[] calldata nlpPools,
        int128[] calldata nlpPoolRebalanceX18
    ) external onlyEndpoint {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);

        ISpotEngine spotEngine = _spotEngine();
        spotEngine.updatePrice(NLP_PRODUCT_ID, oraclePriceX18);

        require(txn.quoteAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        int128 quoteAmount = int128(txn.quoteAmount);
        int128 nlpAmount = quoteAmount.div(oraclePriceX18);

        _validateNlpRebalance(nlpPools, nlpPoolRebalanceX18, quoteAmount);
        for (uint128 i = 0; i < nlpPoolRebalanceX18.length; i++) {
            require(nlpPoolRebalanceX18[i] >= 0, ERR_INVALID_NLP_REBALANCE);
        }

        spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, nlpAmount);
        spotEngine.updateBalance(NLP_PRODUCT_ID, N_ACCOUNT, -nlpAmount);

        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -quoteAmount);
        _applyNlpRebalance(spotEngine, nlpPools, nlpPoolRebalanceX18);

        require(
            getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0,
            ERR_SUBACCT_HEALTH
        );
    }
```

**File:** core/contracts/Clearinghouse.sol (L485-530)
```text
    function burnNlp(
        IEndpoint.BurnNlp calldata txn,
        int128 oraclePriceX18,
        IEndpoint.NlpPool[] calldata nlpPools,
        int128[] calldata nlpPoolRebalanceX18
    ) external onlyEndpoint {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);

        ISpotEngine spotEngine = _spotEngine();
        spotEngine.updatePrice(NLP_PRODUCT_ID, oraclePriceX18);

        require(txn.nlpAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        int128 nlpAmount = int128(txn.nlpAmount);
        require(
            spotEngine.getNlpUnlockedBalance(txn.sender).amount >= nlpAmount,
            ERR_UNLOCKED_NLP_INSUFFICIENT
        );
        int128 quoteAmount = nlpAmount.mul(oraclePriceX18);
        int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);
        quoteAmount = MathHelper.max(0, quoteAmount - burnFee);

        _validateNlpRebalance(nlpPools, nlpPoolRebalanceX18, -quoteAmount);
        for (uint128 i = 0; i < nlpPoolRebalanceX18.length; i++) {
            require(nlpPoolRebalanceX18[i] <= 0, ERR_INVALID_NLP_REBALANCE);
        }

        spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, -nlpAmount);
        spotEngine.updateBalance(NLP_PRODUCT_ID, N_ACCOUNT, nlpAmount);

        if (quoteAmount > 0) {
            spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, quoteAmount);
            _applyNlpRebalance(spotEngine, nlpPools, nlpPoolRebalanceX18);
        }

        require(
            spotEngine.getBalance(NLP_PRODUCT_ID, txn.sender).amount >= 0,
            ERR_SUBACCT_HEALTH
        );
        // Burning NLP can decrease health if the burn fee exceeds the health improvement
        // from the withdrawal. This check prevents malicious actors from deliberately
        // creating unhealthy subaccounts through NLP burns.
        require(
            getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,
            ERR_SUBACCT_HEALTH
        );
    }
```

**File:** core/contracts/common/Errors.sol (L90-96)
```text
string constant ERR_SLIPPAGE_TOO_HIGH = "STH";

string constant ERR_SUBACCOUNT_NOT_FOUND = "SNF";

string constant ERR_INVALID_PRICE = "IPR";

string constant ERR_INVALID_TIME = "ITI";
```
