### Title
`burnNlp` Uses `MAINTENANCE` Health Type Instead of `INITIAL`, Allowing Undercollateralized Positions — (File: `core/contracts/Clearinghouse.sol`)

---

### Summary

`Clearinghouse.burnNlp` enforces a post-burn health check using `HealthType.MAINTENANCE` instead of `HealthType.INITIAL`. Because MAINTENANCE is a strictly weaker constraint than INITIAL, a user can burn NLP tokens and deliberately land in a state where their INITIAL health is negative while their MAINTENANCE health remains non-negative. This violates the protocol invariant that every non-liquidatable subaccount must satisfy INITIAL health ≥ 0, and it is the direct analog of the KodiakIsland wrong-status-assignment bug class.

---

### Finding Description

In `Clearinghouse.burnNlp`, after all balance updates are applied, the final health guard is:

```solidity
require(
    getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,
    ERR_SUBACCT_HEALTH
);
``` [1](#0-0) 

Every other user-initiated operation that reduces collateral uses `HealthType.INITIAL`:

- `mintNlp` → `IProductEngine.HealthType.INITIAL` [2](#0-1) 
- `withdrawCollateral` → `IProductEngine.HealthType.INITIAL` (for non-X_ACCOUNT senders) [3](#0-2) 
- `transferQuote` → `IProductEngine.HealthType.INITIAL` [4](#0-3) 

The `HealthType` enum defines three levels of strictness: `INITIAL` (strictest), `MAINTENANCE`, and `PNL`. [5](#0-4) 

Because INITIAL uses tighter weight factors than MAINTENANCE, there exists a range where `INITIAL health < 0` while `MAINTENANCE health ≥ 0`. The burn fee (`max(ONE, quoteAmount / 1000)`) is the concrete mechanism that can push INITIAL health below zero while leaving MAINTENANCE health non-negative: a user whose INITIAL health margin is smaller than the burn fee will pass the MAINTENANCE check but would fail an INITIAL check.

The in-code comment claims the MAINTENANCE check "prevents malicious actors from deliberately creating unhealthy subaccounts," but this reasoning is self-contradictory: using MAINTENANCE instead of INITIAL is precisely what *permits* creating subaccounts with negative INITIAL health. [6](#0-5) 

---

### Impact Explanation

A subaccount with `INITIAL health < 0` and `MAINTENANCE health ≥ 0` is in a protocol-invariant-violating "gray zone":

1. It cannot be liquidated (MAINTENANCE ≥ 0 means no liquidation trigger).
2. It cannot withdraw collateral or mint NLP (those paths enforce INITIAL health).
3. It can still have orders matched by the sequencer, because `OffchainExchange.isHealthy` unconditionally returns `true` at the contract level. [7](#0-6) 

A user can therefore maintain an undercollateralized position indefinitely. If the position moves further against them, MAINTENANCE health eventually goes negative and liquidation becomes possible — but the protocol has already been exposed to a window of unhedged insolvency risk that the INITIAL health invariant was designed to prevent.

---

### Likelihood Explanation

The trigger is fully user-controlled and requires no special privileges. Any holder of NLP tokens whose INITIAL health margin (after the burn's quote credit) is smaller than the burn fee can reach this state in a single `BurnNlp` slow-mode or sequencer transaction. The burn fee is `max(1e18, quoteAmount / 1000)`, so for any burn of meaningful size the fee is non-trivial. A user who calibrates `nlpAmount` to land exactly in the INITIAL-negative / MAINTENANCE-non-negative band can do so deterministically.

---

### Recommendation

Replace `HealthType.MAINTENANCE` with `HealthType.INITIAL` in the post-burn health check, consistent with every other collateral-reducing operation in the protocol:

```diff
- require(
-     getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,
-     ERR_SUBACCT_HEALTH
- );
+ require(
+     getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0,
+     ERR_SUBACCT_HEALTH
+ );
``` [1](#0-0) 

---

### Proof of Concept

1. Alice holds NLP tokens and has other open positions. Her INITIAL health is `+δ` where `δ < burnFee` for the NLP amount she intends to burn.
2. Alice submits a `BurnNlp` transaction for that amount.
3. `burnNlp` credits her quote balance by `quoteAmount - burnFee`, reducing her INITIAL health to `δ - burnFee < 0`.
4. The MAINTENANCE health check passes because MAINTENANCE weights are more lenient: `MAINTENANCE health ≥ 0`.
5. The transaction succeeds. Alice now holds a subaccount with `INITIAL health < 0` and `MAINTENANCE health ≥ 0`.
6. She cannot be liquidated (MAINTENANCE ≥ 0). She cannot withdraw or mint NLP. But her open positions continue to accrue PnL, and the protocol carries undercollateralized exposure with no liquidation remedy until MAINTENANCE health also turns negative. [8](#0-7)

### Citations

**File:** core/contracts/Clearinghouse.sol (L249-249)
```text
        require(_isAboveInitial(txn.sender), ERR_SUBACCT_HEALTH);
```

**File:** core/contracts/Clearinghouse.sol (L415-419)
```text
        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
```

**File:** core/contracts/Clearinghouse.sol (L479-482)
```text
        require(
            getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0,
            ERR_SUBACCT_HEALTH
        );
```

**File:** core/contracts/Clearinghouse.sol (L485-529)
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
```

**File:** core/contracts/interfaces/engine/IProductEngine.sol (L17-21)
```text
    enum HealthType {
        INITIAL,
        MAINTENANCE,
        PNL
    }
```

**File:** core/contracts/OffchainExchange.sol (L625-629)
```text
    function isHealthy(
        bytes32 /* subaccount */
    ) internal view virtual returns (bool) {
        return true;
    }
```
