### Title
User-Controlled `oraclePriceX18` in `BurnNlp`/`MintNlp` Is Never Validated On-Chain, Enabling NLP Pool Drain - (`EndpointTx.sol` / `Clearinghouse.sol`)

---

### Summary

The `SignedBurnNlp` and `SignedMintNlp` transaction structs each carry an `oraclePriceX18` field that is fully user-controlled and is accepted by the on-chain contracts without any validation against an external oracle or any bounds check. In `burnNlp`, the quote amount returned to the user is computed as `nlpAmount * oraclePriceX18`, and the subsequent health check passes trivially when the price is inflated (burning at a high price always improves health). This allows a user to drain the NLP pool's quote reserves by burning NLP tokens at an arbitrarily inflated price.

---

### Finding Description

In `EndpointTx.sol`, when a `BurnNlp` transaction is processed:

```solidity
priceX18[NLP_PRODUCT_ID] = signedTx.oraclePriceX18;   // line 567
clearinghouse.burnNlp(
    signedTx.tx,
    signedTx.oraclePriceX18,                           // line 570
    nlpPools,
    signedTx.nlpPoolRebalanceX18
);
``` [1](#0-0) 

The `oraclePriceX18` is a field of `SignedBurnNlp` that sits **outside** the core `BurnNlp` struct (which only contains `sender`, `nlpAmount`, `nonce`):

```solidity
struct SignedBurnNlp {
    BurnNlp tx;
    bytes signature;
    int128 oraclePriceX18;       // user-chosen, included in signed bytes
    int128[] nlpPoolRebalanceX18;
}
``` [2](#0-1) 

The user signs the full `transaction` bytes (which include `oraclePriceX18`), so the signature check does not prevent the user from choosing any price they wish. There is no on-chain check that `oraclePriceX18` matches any external oracle or falls within any acceptable range.

Inside `Clearinghouse.burnNlp`:

```solidity
spotEngine.updatePrice(NLP_PRODUCT_ID, oraclePriceX18);   // line 494 — overwrites stored price
int128 quoteAmount = nlpAmount.mul(oraclePriceX18);        // line 502 — quote out ∝ price
int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);
quoteAmount = MathHelper.max(0, quoteAmount - burnFee);
``` [3](#0-2) 

The quote returned to the user is directly proportional to the attacker-supplied `oraclePriceX18`. The NLP pool subaccounts are then debited by this inflated `quoteAmount` via `_applyNlpRebalance`.

The post-burn health check uses `MAINTENANCE` health:

```solidity
require(
    getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,
    ERR_SUBACCT_HEALTH
);
``` [4](#0-3) 

Health is computed as `amount * weight * priceX18` (from `BaseEngine._calculateProductHealth`):

```solidity
health += amount.mul(weight).mul(risk.priceX18);
``` [5](#0-4) 

After `spotEngine.updatePrice(NLP_PRODUCT_ID, oraclePriceX18)` is called, `risk.priceX18` for the NLP product equals the attacker-supplied value. The net health change from burning NLP at inflated price `P_inflated` is:

```
Δhealth = -(nlpAmount × weight × P_inflated)   [NLP removed]
        + (nlpAmount × P_inflated − burnFee)    [quote received]
        = nlpAmount × P_inflated × (1 − weight) − burnFee
```

Since `weight < 1` for maintenance health, `Δhealth > 0` for any positive `P_inflated`. The health check **always passes** regardless of how large `oraclePriceX18` is, because the inflated price simultaneously inflates both the quote received and the NLP value removed, with the quote side winning due to `weight < 1`.

The identical structural flaw exists in `mintNlp` (line 462, 466), where a user-supplied low price yields more NLP tokens per unit of quote paid, though the health check there is harder to pass due to the `INITIAL` health type. [6](#0-5) 

---

### Impact Explanation

A user who holds any amount of NLP tokens can burn them at an arbitrarily inflated `oraclePriceX18`, receiving quote tokens far in excess of the true NLP value. The excess quote is debited from the NLP pool subaccounts, draining the protocol's liquidity pool. The attacker's health check passes trivially. The corrupted state delta is: NLP pool quote reserves decrease by `nlpAmount × (P_inflated − P_true)`, transferred directly to the attacker's quote balance.

---

### Likelihood Explanation

The attack requires only that the user hold NLP tokens (obtainable by minting at the true price) and submit a `BurnNlp` transaction with an inflated `oraclePriceX18`. The sequencer must include the transaction, but the protocol also supports a slow-mode path where users submit transactions directly on-chain; after the 3-day timeout, `executeSlowModeTransaction()` is callable by anyone. No privileged access, oracle compromise, or governance capture is required. [7](#0-6) 

---

### Recommendation

Validate `oraclePriceX18` on-chain against a trusted price source before using it in `mintNlp` / `burnNlp`. At minimum, the contract should:

1. Maintain a sequencer-updated reference price for the NLP product (separate from the user-supplied value).
2. Require that `oraclePriceX18` falls within a bounded deviation (e.g., ±X%) of the reference price before accepting the transaction.
3. Alternatively, remove `oraclePriceX18` from the user-signed struct entirely and have the contract read the current `priceX18[NLP_PRODUCT_ID]` set by the sequencer's most recent `UpdatePrice` transaction.

---

### Proof of Concept

1. Attacker mints NLP at the true price `P` by submitting a valid `MintNlp` with `oraclePriceX18 = P`, paying `Q` quote and receiving `Q / P` NLP tokens.
2. Attacker signs a `BurnNlp` transaction with `oraclePriceX18 = P * 1000` and `nlpPoolRebalanceX18` summing to `-(nlpAmount * P * 1000 - burnFee)`.
3. Attacker submits this as a slow-mode transaction via `submitSlowModeTransaction`.
4. After 3 days, attacker calls `executeSlowModeTransaction()`.
5. `burnNlp` executes: attacker receives `nlpAmount * P * 1000 - burnFee` quote; NLP pool subaccounts are debited by this amount.
6. Health check passes because `Δhealth = nlpAmount * P * 1000 * (1 - weight) - burnFee > 0`.
7. Net profit ≈ `nlpAmount * P * 999`, extracted from the NLP pool at the expense of all NLP liquidity providers.

### Citations

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

**File:** core/contracts/interfaces/IEndpoint.sol (L131-136)
```text
    struct SignedBurnNlp {
        BurnNlp tx;
        bytes signature;
        int128 oraclePriceX18;
        int128[] nlpPoolRebalanceX18;
    }
```

**File:** core/contracts/Clearinghouse.sol (L461-466)
```text
        ISpotEngine spotEngine = _spotEngine();
        spotEngine.updatePrice(NLP_PRODUCT_ID, oraclePriceX18);

        require(txn.quoteAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        int128 quoteAmount = int128(txn.quoteAmount);
        int128 nlpAmount = quoteAmount.div(oraclePriceX18);
```

**File:** core/contracts/Clearinghouse.sol (L493-504)
```text
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
```

**File:** core/contracts/Clearinghouse.sol (L526-529)
```text
        require(
            getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,
            ERR_SUBACCT_HEALTH
        );
```

**File:** core/contracts/BaseEngine.sol (L174-174)
```text
            health += amount.mul(weight).mul(risk.priceX18);
```

**File:** core/contracts/Endpoint.sol (L231-236)
```text
    function executeSlowModeTransaction() external {
        SlowModeConfig memory _slowModeConfig = slowModeConfig;
        _executeSlowModeTransaction(_slowModeConfig, false);
        nSubmissions += 1;
        slowModeConfig = _slowModeConfig;
    }
```
