### Title
User-Controlled `oraclePriceX18` in `SignedMintNlp`/`SignedBurnNlp` Enables Oracle Price Manipulation via Slow Mode, Draining NLP Pools — (`core/contracts/Clearinghouse.sol`)

---

### Summary

`mintNlp` and `burnNlp` use an `oraclePriceX18` value that is embedded inside the user-signed transaction (`SignedMintNlp` / `SignedBurnNlp`). Because the on-chain contract performs no bounds check or oracle validation on this field, and because `MintNlp`/`BurnNlp` transactions can be submitted and self-executed via the permissionless slow-mode path after a 3-day delay, an unprivileged user can mint NLP at an arbitrarily low oracle price and burn NLP at an arbitrarily high oracle price, extracting the difference from NLP pool subaccounts.

---

### Finding Description

`SignedMintNlp` and `SignedBurnNlp` each carry `oraclePriceX18` as a top-level field alongside the user's signature:

```solidity
struct SignedMintNlp {
    MintNlp tx;
    bytes signature;
    int128 oraclePriceX18;      // user-supplied, signed by user
    int128[] nlpPoolRebalanceX18;
}
``` [1](#0-0) 

`validateSignedTx` is called with the full `transaction` bytes (which include `oraclePriceX18`), so the user's signature covers whatever price they chose:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,          // full bytes including oraclePriceX18
    signedTx.signature,
    true
);
``` [2](#0-1) 

`Clearinghouse.mintNlp` then uses this price directly to compute how many NLP tokens to mint:

```solidity
int128 nlpAmount = quoteAmount.div(oraclePriceX18);
``` [3](#0-2) 

And `burnNlp` uses it to compute how much USDC to return:

```solidity
int128 quoteAmount = nlpAmount.mul(oraclePriceX18);
``` [4](#0-3) 

There is no on-chain check that `oraclePriceX18` is within any acceptable range or matches any stored oracle value.

`MintNlp` and `BurnNlp` are **not** in the owner-only list inside `submitSlowModeTransactionImpl`, so any user can enqueue them via `submitSlowModeTransaction` for a small fee:

```solidity
} else if (
    txType == IEndpoint.TransactionType.WithdrawInsurance ||
    ...
    txType == IEndpoint.TransactionType.ForceRebalanceNlpPool ||
    ...
) {
    require(sender == owner());
} else {
    chargeSlowModeFee(_getQuote(), sender);   // MintNlp / BurnNlp fall here
    slowModeFees += SLOW_MODE_FEE;
}
``` [5](#0-4) 

After the 3-day delay, the user calls `executeSlowModeTransaction` (no access control) and the contract processes the transaction with the user's embedded `oraclePriceX18` unchanged.

`burnNlp` checks only the **burner's** health, never the health of the NLP pool subaccounts that are debited:

```solidity
require(
    getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,
    ERR_SUBACCT_HEALTH
);
``` [6](#0-5) 

`_applyNlpRebalance` can therefore push pool subaccount USDC balances arbitrarily negative with no revert. [7](#0-6) 

---

### Impact Explanation

An attacker can:

1. **Mint NLP at `oraclePriceX18 = ε` (near-zero):** Pay `Q` USDC, receive `Q / ε` NLP tokens. The NLP price is set to `ε`, so the health contribution of the NLP tokens equals `(Q/ε) * weight * ε = Q * weight`, which passes the initial health check.
2. **Burn NLP at `oraclePriceX18 = P_market`:** Burn `Q / ε` NLP tokens, receive `(Q / ε) * P_market` USDC from NLP pools. The burner's health is positive (large USDC balance). No pool health check is performed.

Net extraction: `(Q / ε) * P_market − Q` USDC, which grows without bound as `ε → 0`. NLP pool subaccounts are driven deeply negative, making them insolvent and unable to honour future LP withdrawals or trading obligations. All NLP liquidity providers suffer permanent loss.

---

### Likelihood Explanation

The slow-mode path is permissionless and censorship-resistant by design. Any user with a subaccount can submit a `MintNlp` or `BurnNlp` slow-mode transaction with an arbitrary `oraclePriceX18`. The sequencer cannot alter the signed bytes without invalidating the signature. After the 3-day delay the user self-executes. No privileged access, leaked keys, or governance capture is required.

---

### Recommendation

`oraclePriceX18` must not be a user-supplied field. Two complementary fixes:

1. **Remove `oraclePriceX18` from the user-signed payload.** Move it outside the signed region (analogous to how `feeX18` is handled in `SignedWithdrawCollateralV2`) so the sequencer injects it at submission time and it is not covered by the user's signature.
2. **Add an on-chain bounds check** in `mintNlp` and `burnNlp` against the stored `priceX18[NLP_PRODUCT_ID]`, rejecting any `oraclePriceX18` that deviates beyond a configurable tolerance (e.g., ±5%).

Additionally, `burnNlp` should verify that each affected NLP pool subaccount remains above maintenance health after the rebalance, mirroring the check already present in `forceRebalanceNlpPool`. [8](#0-7) 

---

### Proof of Concept

Assume market NLP price = 1 USDC, NLP `longWeightInitial` ≈ 1, pool has 10,000 USDC.

**Step 1 – Slow-mode MintNlp (price = 0.000001):**
```
quoteAmount  = 1 USDC
oraclePriceX18 = 0.000001
nlpAmount    = 1 / 0.000001 = 1,000,000 NLP tokens
Health after mint: 1,000,000 * 1 * 0.000001 - 1 = 0  ✓
```

**Step 2 – Wait 3 days, execute slow-mode transaction.**

**Step 3 – Slow-mode BurnNlp (price = 1):**
```
nlpAmount    = 1,000,000
oraclePriceX18 = 1
quoteAmount  = 1,000,000 * 1 = 1,000,000 USDC (minus 0.1% fee ≈ 999,000 USDC)
Pool USDC balance: 10,000 - 999,000 = -989,000  (no pool health check)
Attacker net profit: 999,000 - 1 = 998,999 USDC
```

**Step 4 – Wait 3 days, execute slow-mode transaction.**

The attacker paid 1 USDC and extracted ~999,000 USDC, leaving the NLP pool insolvent.

### Citations

**File:** core/contracts/interfaces/IEndpoint.sol (L118-123)
```text
    struct SignedMintNlp {
        MintNlp tx;
        bytes signature;
        int128 oraclePriceX18;
        int128[] nlpPoolRebalanceX18;
    }
```

**File:** core/contracts/EndpointTx.sol (L355-372)
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

**File:** core/contracts/Clearinghouse.sol (L439-451)
```text
    function _applyNlpRebalance(
        ISpotEngine spotEngine,
        IEndpoint.NlpPool[] calldata nlpPools,
        int128[] calldata nlpPoolRebalanceX18
    ) internal {
        for (uint128 i = 0; i < nlpPoolRebalanceX18.length; i++) {
            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                nlpPools[i].subaccount,
                nlpPoolRebalanceX18[i]
            );
        }
    }
```

**File:** core/contracts/Clearinghouse.sol (L466-466)
```text
        int128 nlpAmount = quoteAmount.div(oraclePriceX18);
```

**File:** core/contracts/Clearinghouse.sol (L502-502)
```text
        int128 quoteAmount = nlpAmount.mul(oraclePriceX18);
```

**File:** core/contracts/Clearinghouse.sol (L526-529)
```text
        require(
            getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,
            ERR_SUBACCT_HEALTH
        );
```

**File:** core/contracts/Clearinghouse.sol (L532-548)
```text
    function forceRebalanceNlpPool(
        IEndpoint.NlpPool[] calldata nlpPools,
        int128[] calldata nlpPoolRebalanceX18
    ) external onlyEndpoint {
        _validateNlpRebalance(nlpPools, nlpPoolRebalanceX18, 0);
        ISpotEngine spotEngine = _spotEngine();
        _applyNlpRebalance(spotEngine, nlpPools, nlpPoolRebalanceX18);

        for (uint128 i = 1; i < nlpPools.length; i++) {
            require(
                getHealth(
                    nlpPools[i].subaccount,
                    IProductEngine.HealthType.INITIAL
                ) >= 0,
                ERR_SUBACCT_HEALTH
            );
        }
```
