### Title
Stale Sequencer Price Used in Slow-Mode Withdrawal Health Checks Enables Undercollateralized Withdrawals — (`core/contracts/Endpoint.sol`, `core/contracts/Clearinghouse.sol`, `core/contracts/BaseEngine.sol`)

---

### Summary

Nado's on-chain price state (`priceX18`) is exclusively updated by the sequencer via `UpdatePrice` transactions. No timestamp is stored alongside the price, and no staleness check exists anywhere in the on-chain contracts. The public `executeSlowModeTransaction()` function, callable by any user after a 3-day delay, processes `WithdrawCollateral` transactions whose health checks consume this potentially stale price. If the sequencer goes offline, prices freeze at their last-known value. A user with a pending slow-mode withdrawal can then execute it against a stale (pre-crash) price, bypassing the health check and withdrawing collateral that would otherwise be locked, leaving the system undercollateralized.

---

### Finding Description

**Price storage — no timestamp, no staleness guard**

`EndpointStorage.sol` stores prices as a bare mapping with no associated update timestamp:

```solidity
mapping(uint32 => int128) internal priceX18;
```

`Endpoint.getPriceX18` returns the stored value unconditionally:

```solidity
function getPriceX18(uint32 productId) public override returns (int128 _priceX18) {
    _priceX18 = priceX18[productId];
    require(_priceX18 != 0, ERR_INVALID_PRODUCT);
    emit PriceQuery(productId);
}
```

The only way `priceX18[productId]` is updated on-chain is through a sequencer-submitted `UpdatePrice` transaction processed in `EndpointTx.processTransactionImpl`:

```solidity
} else if (txType == IEndpoint.TransactionType.UpdatePrice) {
    (uint32 productId, int128 newPriceX18) = clearinghouse.updatePrice(transaction);
    if (productId != 0) {
        priceX18[productId] = newPriceX18;
    }
}
```

If the sequencer stops submitting transactions, `priceX18` values are permanently frozen at their last-known state.

**Public slow-mode execution path**

`Endpoint.executeSlowModeTransaction()` is unrestricted — any address can call it after the 3-day `executableAt` delay:

```solidity
function executeSlowModeTransaction() external {
    SlowModeConfig memory _slowModeConfig = slowModeConfig;
    _executeSlowModeTransaction(_slowModeConfig, false);
    nSubmissions += 1;
    slowModeConfig = _slowModeConfig;
}
```

`_executeSlowModeTransaction` only checks `txn.executableAt <= block.timestamp`; it has no awareness of price freshness.

**Health check consumes stale price**

When a slow-mode `WithdrawCollateral` is processed, `Clearinghouse.withdrawCollateral` deducts the balance and then gates the transfer on a health check:

```solidity
require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
```

`getHealth` calls `BaseEngine._calculateProductHealth`, which reads `_risk(productId).priceX18` — the same value set by the sequencer's last `UpdatePrice` transaction:

```solidity
health += amount.mul(weight).mul(risk.priceX18);
```

`_risk().value[productId].priceX18` is written only by `BaseEngine.updatePrice`, which is called exclusively from `Clearinghouse.updatePrice` (sequencer path). There is no fallback, no circuit breaker, and no staleness window enforced anywhere in the call chain.

---

### Impact Explanation

**Undercollateralized withdrawal (solvency corruption):** A user who has a pending slow-mode `WithdrawCollateral` transaction when the sequencer goes offline can call `executeSlowModeTransaction` after 3 days. If the collateral asset's market price has dropped significantly during the outage, the health check uses the stale (pre-drop) price, making the account appear healthier than it actually is. The withdrawal succeeds, and the system is left holding less collateral than the liabilities it backs.

**Liquidation suppression:** Accounts that should be liquidatable at the current market price appear healthy at the stale price. Liquidators cannot act (the sequencer is offline and cannot submit `LiquidateSubaccount` transactions), and the system accumulates bad debt silently.

Both effects are analogous to the original report: the system cannot react to extreme price movements and may become undercollateralized.

---

### Likelihood Explanation

**Low.** The sequencer must be offline long enough for (a) prices to diverge materially and (b) the 3-day slow-mode delay to expire. However:
- The sequencer is a single centralized component; any unplanned downtime (infrastructure failure, network partition) creates the window.
- A user does not need to predict the outage in advance — any pre-existing slow-mode withdrawal queued before the outage becomes exploitable once the delay expires.
- The 3-day window is long enough for significant price movement in volatile markets.

---

### Recommendation

1. **Store a price update timestamp** alongside each `priceX18` entry and reject health-check calls (or revert slow-mode execution) when the price is older than a configurable staleness threshold.
2. **Gate `executeSlowModeTransaction` on price freshness** — if the last `UpdatePrice` submission is older than the staleness threshold, revert or skip withdrawal transactions until the sequencer resumes.
3. Consider a **fallback price path** (e.g., a secondary on-chain oracle) that can be activated when the sequencer has been silent beyond the threshold, analogous to the fallback oracle recommended in the original report.

---

### Proof of Concept

1. Alice has 1 ETH of collateral (priced at $3,000 by the sequencer) and a $2,900 USDC liability — she is just above the initial health threshold.
2. Alice submits a `WithdrawCollateral` slow-mode transaction for 0.9 ETH. The sequencer queues it.
3. The sequencer goes offline. ETH market price drops to $1,500. The sequencer never submits an `UpdatePrice` transaction, so `priceX18[ETH_PRODUCT_ID]` remains $3,000.
4. After 3 days, Alice calls `Endpoint.executeSlowModeTransaction()`.
5. `Clearinghouse.withdrawCollateral` deducts 0.9 ETH from Alice's balance, then calls `getHealth`. The health check uses the stale $3,000 price: Alice's remaining 0.1 ETH is valued at $300, which combined with her $2,900 liability yields health ≈ −$2,600 at true prices — but at the stale price it appears as $300 − $2,900 = −$2,600... 

   Adjusting the example for clarity: Alice has 2 ETH ($6,000 stale value) and a $5,500 liability. She withdraws 1.9 ETH. At stale price, remaining 0.1 ETH = $300; health = $300 − $5,500 = −$5,200 — this would revert. The realistic exploit is a position that is healthy at the stale price but underwater at the true price: Alice has 2 ETH ($6,000 stale, $3,000 true) and a $2,500 liability. She withdraws 1.5 ETH. At stale price: 0.5 ETH × $3,000 = $1,500 − $2,500 = −$1,000 → reverts. She withdraws 0.5 ETH. At stale price: 1.5 ETH × $3,000 = $4,500 − $2,500 = $2,000 ≥ 0 → passes. At true price: 1.5 ETH × $1,500 = $2,250 − $2,500 = −$250 → would have been blocked. Alice extracts collateral that leaves her account underwater at true market prices.

6. The system is now undercollateralized by the difference between the stale and true collateral value.

**Exact root-cause chain:**
- `Endpoint.executeSlowModeTransaction` (no price-freshness guard) [1](#0-0) 
- → `Clearinghouse.withdrawCollateral` health check [2](#0-1) 
- → `BaseEngine._calculateProductHealth` reads `risk.priceX18` with no staleness check [3](#0-2) 
- `priceX18` updated exclusively by sequencer-submitted `UpdatePrice` transactions [4](#0-3) 
- No timestamp stored alongside price [5](#0-4)

### Citations

**File:** core/contracts/Endpoint.sol (L231-236)
```text
    function executeSlowModeTransaction() external {
        SlowModeConfig memory _slowModeConfig = slowModeConfig;
        _executeSlowModeTransaction(_slowModeConfig, false);
        nSubmissions += 1;
        slowModeConfig = _slowModeConfig;
    }
```

**File:** core/contracts/Clearinghouse.sol (L415-419)
```text
        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
```

**File:** core/contracts/BaseEngine.sol (L162-176)
```text
        RiskHelper.Risk memory risk = _risk(productId);
        (int128 amount, int128 quoteAmount) = _getBalance(
            productId,
            subaccount
        );
        int128 weight = RiskHelper._getWeightX18(risk, amount, healthType);
        health += quoteAmount;

        if (amount != 0) {
            if (weight == 2 * ONE) {
                return -INF;
            }
            health += amount.mul(weight).mul(risk.priceX18);
            emit PriceQuery(productId);
        }
```

**File:** core/contracts/EndpointTx.sol (L486-492)
```text
        } else if (txType == IEndpoint.TransactionType.UpdatePrice) {
            (uint32 productId, int128 newPriceX18) = clearinghouse.updatePrice(
                transaction
            );
            if (productId != 0) {
                priceX18[productId] = newPriceX18;
            }
```

**File:** core/contracts/EndpointStorage.sol (L60-60)
```text
    mapping(uint32 => int128) internal priceX18;
```
