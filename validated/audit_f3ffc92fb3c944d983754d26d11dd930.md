### Title
NLP Burn Fee Creates Inconsistency Between Health Valuation and Actual Burn Proceeds — (File: `core/contracts/Clearinghouse.sol`)

---

### Summary

The `burnNlp` function deducts a burn fee from the quote returned to the user, but the health calculation in `BaseEngine._calculateProductHealth` values NLP tokens at the full oracle price with no fee adjustment. This is a direct analog to the `StableSwapOperatorV1.resistantBalanceAndFei` bug: three operations — mint, burn, and health valuation — use three different effective prices for the same NLP position, causing the protocol's health accounting to overstate the realizable value of NLP collateral.

---

### Finding Description

**Mint** (`Clearinghouse.sol`, `mintNlp`): [1](#0-0) 

```
nlpAmount = quoteAmount / oraclePriceX18   // no fee; effective price = oraclePriceX18
```

**Burn** (`Clearinghouse.sol`, `burnNlp`): [2](#0-1) 

```
burnFee    = max(ONE, quoteAmount / 1000)  // flat 1 USDC minimum OR 0.1%
quoteAmount = max(0, quoteAmount - burnFee) // effective price < oraclePriceX18
```

**Health valuation** (`BaseEngine.sol`, `_calculateProductHealth`): [3](#0-2) 

```
health += amount.mul(weight).mul(risk.priceX18)  // risk.priceX18 = oraclePriceX18, no fee
```

`risk.priceX18` is set to `oraclePriceX18` by `updatePrice` during mint/burn: [4](#0-3) 

The three effective prices are therefore:

| Operation | Effective price |
|---|---|
| Mint | `oraclePriceX18` |
| Burn | `oraclePriceX18 − max(1 USDC, 0.1%)` |
| Health | `oraclePriceX18 × weight` |

When `longWeightMaintenance = 1e9` (i.e., weight = 1.0, the maximum allowed by `_addOrUpdateProduct`): [5](#0-4) 

health reports the full oracle price, but burn yields `oraclePriceX18 − burnFee`. For a 10 USDC NLP position the flat 1 USDC minimum fee is a **10 % overestimate**; for a 100 USDC position it is 1 %; for positions above 1 000 USDC it is 0.1 %.

---

### Impact Explanation

A user who borrows against an NLP position up to the INITIAL health limit may find that burning the NLP to repay the loan is blocked: the burn fee pushes MAINTENANCE health below zero, causing the `burnNlp` health check to revert: [6](#0-5) 

The user is effectively locked into the NLP position until they first repay enough debt to absorb the fee — a state the health calculation never predicted. In a liquidation context, if the liquidation path involves burning NLP, the fee reduces proceeds below what the health model assumed, potentially leaving residual bad debt that the insurance fund must absorb.

---

### Likelihood Explanation

The inconsistency is always present whenever `longWeightMaintenance` for `NLP_PRODUCT_ID` is set at or near `1e9`. It is triggered by any user who mints NLP, borrows against it at the health-calculated value, and then attempts to burn. Small positions (< 1 000 USDC) are most affected due to the flat 1 USDC floor fee. No special privileges are required; the entry path is the standard `mintNlp` → borrow → `burnNlp` sequence through the `Endpoint`.

---

### Recommendation

1. Adjust the NLP health weight downward by at least the maximum fee fraction (e.g., set `longWeightMaintenance ≤ 0.999e9`) so the health model is always more conservative than the worst-case burn proceeds.
2. Alternatively, introduce a fee-adjusted price for NLP in `_calculateProductHealth` so that mint, burn, and health valuation all use a consistent effective price.
3. Ensure that `mintNlp`, `burnNlp`, and the health calculation are reviewed together whenever fee parameters change, to preserve the invariant: `health_value ≤ actual_burn_proceeds`.

---

### Proof of Concept

1. User calls `mintNlp` with `quoteAmount = 10e18` (10 USDC). Receives `10e18 / oraclePriceX18` NLP tokens. `risk.priceX18` is set to `oraclePriceX18`.
2. Health calculation: `nlpAmount × 1.0 × oraclePriceX18 = 10 USDC`. User borrows 9.5 USDC; INITIAL health = +0.5 USDC — passes.
3. User calls `burnNlp`. `quoteAmount = 10 USDC`. `burnFee = max(1e18, 10e18/1000) = 1e18` (1 USDC). Proceeds = 9 USDC.
4. Post-burn state: NLP = 0, quote delta = +9 USDC, outstanding loan = −9.5 USDC. MAINTENANCE health = −0.5 USDC.
5. `burnNlp` reverts at: [6](#0-5) 
6. The user is locked: the health model promised 10 USDC of realizable value, but the burn only yields 9 USDC. The user must repay ≥ 0.5 USDC of debt before the burn can succeed — a state the health accounting never anticipated.

### Citations

**File:** core/contracts/Clearinghouse.sol (L465-466)
```text
        int128 quoteAmount = int128(txn.quoteAmount);
        int128 nlpAmount = quoteAmount.div(oraclePriceX18);
```

**File:** core/contracts/Clearinghouse.sol (L502-504)
```text
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

**File:** core/contracts/BaseEngine.sol (L235-241)
```text
        require(
            riskStore.longWeightInitial <= riskStore.longWeightMaintenance &&
                riskStore.longWeightMaintenance <= 10**9 &&
                riskStore.shortWeightInitial >=
                riskStore.shortWeightMaintenance &&
                riskStore.shortWeightMaintenance >= 10**9,
            ERR_BAD_PRODUCT_CONFIG
```

**File:** core/contracts/BaseEngine.sol (L273-276)
```text
    function updatePrice(uint32 productId, int128 priceX18) external virtual {
        require(msg.sender == address(_clearinghouse), ERR_UNAUTHORIZED);
        _risk().value[productId].priceX18 = priceX18;
    }
```
