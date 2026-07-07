### Title
Stale Sequencer-Fed Price Enables Undercollateralized Slow Mode Withdrawal During Sequencer Downtime — (File: `core/contracts/Clearinghouse.sol`)

---

### Summary

Nado's `priceX18` values are exclusively updated by the off-chain sequencer via `UpdatePrice` transactions. When the sequencer is unavailable, these prices freeze at their last known value. The slow mode mechanism allows any user to bypass the sequencer after a 3-day delay (`SLOW_MODE_TX_DELAY`). The health check executed at slow mode withdrawal time reads the frozen `priceX18` from the risk store, with no staleness guard. If market prices have moved adversely during sequencer downtime, a user can execute a withdrawal that passes the stale-price health check but leaves the account undercollateralized at real market prices, creating bad debt for the protocol.

---

### Finding Description

Nado prices are set entirely by the sequencer. The `UpdatePrice` transaction path is:

`EndpointTx.processTransactionImpl` → `clearinghouse.updatePrice()` → `engine.updatePrice(productId, priceX18)` → stored in `_risk().value[productId].priceX18` [1](#0-0) 

When the sequencer stops submitting transactions, `priceX18` in the risk store is frozen at the last sequencer-provided value. There is no on-chain timestamp recorded alongside the price, and no staleness check anywhere in the withdrawal path.

The slow mode mechanism is the protocol's explicit fallback for sequencer unavailability. Any user can submit a `WithdrawCollateral` slow mode transaction via `submitSlowModeTransaction`, and after `SLOW_MODE_TX_DELAY` (3 days), anyone can call `executeSlowModeTransaction()` to process it without sequencer involvement: [2](#0-1) [3](#0-2) 

The slow mode `WithdrawCollateral` path calls `clearinghouse.withdrawCollateral()`, which performs a health check: [4](#0-3) [5](#0-4) 

`getHealth()` calls `spotEngine.getHealthContribution()` and `perpEngine.getHealthContribution()`, both of which read `_risk(productId).priceX18` — the frozen sequencer-fed price: [6](#0-5) 

There is no check that the price was updated recently, no `block.timestamp` comparison, and no sequencer liveness guard anywhere in this path.

---

### Impact Explanation

A user whose collateral asset has declined in value during sequencer downtime can submit a slow mode withdrawal, wait 3 days, and execute it. The health check passes because it uses the pre-downtime (inflated) price. The user withdraws collateral that is actually insufficient to cover their liabilities at current market prices. The protocol absorbs the resulting bad debt. This is a direct asset loss to the protocol's insurance fund and counterparties.

---

### Likelihood Explanation

The Nado sequencer is a centralized off-chain component. Infrastructure failures, maintenance windows, or network issues can cause multi-day outages. The slow mode mechanism is explicitly designed for this scenario, making the attack path reachable without any privileged access. The attacker only needs to pay a $1 slow mode fee and wait 3 days. Volatile market conditions (which often coincide with infrastructure stress) amplify the price divergence and the profit opportunity.

---

### Recommendation

At the time `executeSlowModeTransaction()` processes a `WithdrawCollateral`, the contract should verify that the price used in the health check is not stale. Concretely:

1. Record a `lastPriceUpdateTimestamp[productId]` alongside each `UpdatePrice` write.
2. In `withdrawCollateral` (or its slow mode entry point), require that `block.timestamp - lastPriceUpdateTimestamp[productId] <= MAX_PRICE_AGE` for every product in the subaccount's portfolio before proceeding.
3. If any price is stale, revert the withdrawal until the sequencer resumes and refreshes prices.

This mirrors the fix recommended in H-01: ensure that the price used for critical financial decisions reflects a recent observation, not a frozen historical value.

---

### Proof of Concept

1. Sequencer goes offline. `priceX18[ETH_PRODUCT_ID]` is frozen at `$3000e18`.
2. ETH market price falls to `$2000`.
3. Attacker holds 2 ETH collateral (`$6000` at stale price) and `$4500` USDC debt. At stale price, initial health > 0. At real price, health < 0 (undercollateralized).
4. Attacker calls `submitSlowModeTransaction(WithdrawCollateral{productId: ETH, amount: 1e18})`, paying the $1 slow mode fee. [7](#0-6) 
5. After 3 days, attacker calls `executeSlowModeTransaction()`. [8](#0-7) 
6. `processSlowModeTransactionImpl` routes to `clearinghouse.withdrawCollateral()`. [4](#0-3) 
7. `getHealth()` computes health using `priceX18 = $3000e18` (stale). Health appears positive. Withdrawal succeeds. [5](#0-4) 
8. Attacker receives 1 ETH (worth $2000 at market). Remaining collateral: 1 ETH = $2000. Debt: $4500. Protocol has $2500 bad debt.

### Citations

**File:** core/contracts/EndpointTx.sol (L217-229)
```text
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
            IEndpoint.WithdrawCollateral memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.WithdrawCollateral)
            );
            validateSender(txn.sender, sender);
            clearinghouse.withdrawCollateral(
                txn.sender,
                txn.productId,
                txn.amount,
                address(0),
                nSubmissions
            );
```

**File:** core/contracts/EndpointTx.sol (L376-380)
```text
        slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: transaction
        });
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

**File:** core/contracts/Endpoint.sol (L196-199)
```text
        require(
            fromSequencer || (txn.executableAt <= block.timestamp),
            ERR_SLOW_TX_TOO_RECENT
        );
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

**File:** core/contracts/common/Constants.sol (L50-50)
```text
uint64 constant SLOW_MODE_TX_DELAY = 3 * 24 * 60 * 60; // 3 days
```

**File:** core/contracts/Clearinghouse.sol (L415-419)
```text
        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
```

**File:** core/contracts/BaseEngine.sol (L50-60)
```text
    function _risk(uint32 productId)
        internal
        returns (RiskHelper.Risk memory r)
    {
        RiskHelper.RiskStore memory s = _risk().value[productId];
        r.longWeightInitialX18 = int128(s.longWeightInitial) * 1e9;
        r.shortWeightInitialX18 = int128(s.shortWeightInitial) * 1e9;
        r.longWeightMaintenanceX18 = int128(s.longWeightMaintenance) * 1e9;
        r.shortWeightMaintenanceX18 = int128(s.shortWeightMaintenance) * 1e9;
        r.priceX18 = s.priceX18;
    }
```
