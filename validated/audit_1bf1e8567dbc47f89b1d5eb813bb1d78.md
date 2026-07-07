### Title
Stale Oracle Price Used in Slow Mode Withdrawal Health Check — (`core/contracts/Clearinghouse.sol`, `core/contracts/BaseEngine.sol`)

---

### Summary

The `withdrawCollateral` function, reachable by any user via the slow mode path without sequencer involvement, performs a health check using `_risk(productId).priceX18` — a price value that is exclusively updated by the sequencer. Because slow mode is specifically designed to operate when the sequencer is offline or censoring, the price used in the health check will be stale by at least the 3-day slow mode delay. A user whose collateral has declined in value can exploit the inflated stale price to pass the health check and withdraw collateral they are no longer entitled to, leaving the protocol undercollateralized.

---

### Finding Description

Nado stores oracle prices in two locations, both updated exclusively by the sequencer:

1. `priceX18[productId]` in `EndpointStorage`, updated in `processTransactionImpl` when a `TransactionType.UpdatePrice` transaction is processed.
2. `_risk().value[productId].priceX18` in `BaseEngine`, updated via `engine.updatePrice()` called from `clearinghouse.updatePrice()`. [1](#0-0) [2](#0-1) 

Both stores are sequencer-gated. There is no mechanism for prices to be refreshed on-chain by any other actor.

The slow mode withdrawal path is:

1. Any user calls `Endpoint.submitSlowModeTransaction(WithdrawCollateral)` — no sequencer required.
2. After `SLOW_MODE_TX_DELAY` (3 days), any user calls `Endpoint.executeSlowModeTransaction()`.
3. This dispatches to `processSlowModeTransactionImpl` → `clearinghouse.withdrawCollateral()`. [3](#0-2) [4](#0-3) 

Inside `withdrawCollateral`, the health check is: [5](#0-4) 

`getHealth` calls `spotEngine.getHealthContribution` and `perpEngine.getHealthContribution`, which both call `_calculateProductHealth`: [6](#0-5) 

The price used at line 174 is `risk.priceX18`, which comes from `_risk(productId).priceX18` — the sequencer-set value. There is no call to update or refresh this price before reading it. `Endpoint.getPriceX18` similarly just reads from storage: [7](#0-6) 

The slow mode mechanism exists precisely for the scenario where the sequencer is unavailable. In that scenario, `UpdatePrice` transactions cannot be submitted, so `_risk(productId).priceX18` is frozen at the last value the sequencer set — potentially days old.

---

### Impact Explanation

If a user holds collateral (e.g., a volatile spot asset) whose market price has dropped significantly during the sequencer outage, the stale (inflated) price stored in `_risk(productId).priceX18` will cause `getHealth` to return a falsely positive health value. The user passes the `require(getHealth(sender, healthType) >= 0)` check and withdraws collateral they are no longer entitled to under current market prices. The protocol is left with a subaccount whose actual health is negative, creating a bad debt that must be covered by the insurance fund or socialized across LPs.

Simultaneously, `LiquidateSubaccount` is not available via slow mode — it is only processed by the sequencer through `processTransactionImpl`. This means that during a sequencer outage, undercollateralized positions cannot be liquidated, compounding the risk: the protocol cannot defend itself while users can exploit stale prices to exit. [8](#0-7) 

---

### Likelihood Explanation

The slow mode delay is hardcoded to 3 days (`SLOW_MODE_TX_DELAY`). Any sequencer outage or censorship event lasting 3 days activates this path. The slow mode mechanism is an explicit protocol design feature for censorship resistance, so the scenario is not hypothetical — it is the intended use case of the feature. During high-volatility periods (which are also the most likely times for network congestion or sequencer issues), a 3-day price drift on volatile assets can be substantial (10–50%+), making the exploit economically meaningful.

---

### Recommendation

Before executing a slow mode withdrawal, the protocol should either:

1. **Reject slow mode withdrawals for non-quote products** if the price has not been updated within a configurable staleness threshold (e.g., require `block.timestamp - lastPriceUpdateTime[productId] < MAX_PRICE_AGE`).
2. **Integrate an on-chain price oracle** (e.g., Chainlink or a TWAP) that can be read permissionlessly during slow mode execution, so the health check uses a fresh price rather than the sequencer-cached value.
3. **Disallow slow mode withdrawals for non-USDC collateral** entirely, limiting slow mode to quote-only withdrawals where no price oracle is needed.

---

### Proof of Concept

1. Sequencer goes offline. Last recorded price for ETH (productId = 1): `priceX18[1] = 3000e18`.
2. ETH market price drops to `1000e18` over 3 days.
3. Attacker has subaccount with: 1 ETH spot balance, 2000 USDC borrow (quote balance = -2000e18).
4. Actual health at $1000: `1 * 1000 * longWeightInitial - 2000 < 0` → insolvent.
5. Stale health at $3000: `1 * 3000 * longWeightInitial - 2000 > 0` → passes check.
6. Attacker submitted `WithdrawCollateral(productId=QUOTE, amount=500)` 3 days ago.
7. Attacker calls `Endpoint.executeSlowModeTransaction()`.
8. `clearinghouse.withdrawCollateral()` calls `getHealth` → reads `_risk(1).priceX18 = 3000e18` → health is positive → withdrawal succeeds.
9. Attacker receives 500 USDC. Protocol holds 1 ETH worth $1000 against a 2500 USDC liability — a $1500 shortfall. [9](#0-8) [10](#0-9)

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

**File:** core/contracts/BaseEngine.sol (L157-177)
```text
    function _calculateProductHealth(
        uint32 productId,
        bytes32 subaccount,
        IProductEngine.HealthType healthType
    ) internal returns (int128 health) {
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
    }
```

**File:** core/contracts/BaseEngine.sol (L273-276)
```text
    function updatePrice(uint32 productId, int128 priceX18) external virtual {
        require(msg.sender == address(_clearinghouse), ERR_UNAUTHORIZED);
        _risk().value[productId].priceX18 = priceX18;
    }
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

**File:** core/contracts/Endpoint.sol (L334-342)
```text
    function getPriceX18(uint32 productId)
        public
        override
        returns (int128 _priceX18)
    {
        _priceX18 = priceX18[productId];
        require(_priceX18 != 0, ERR_INVALID_PRODUCT);
        emit PriceQuery(productId);
    }
```

**File:** core/contracts/Clearinghouse.sol (L71-139)
```text
    function getHealth(bytes32 subaccount, IProductEngine.HealthType healthType)
        public
        returns (int128 health)
    {
        ISpotEngine spotEngine = _spotEngine();
        IPerpEngine perpEngine = _perpEngine();

        health = spotEngine.getHealthContribution(subaccount, healthType);
        // min health means that it is attempting to borrow a spot that exists outside
        // of the risk system -- return min health to error out this action
        if (health == -INF) {
            return health;
        }
        health += perpEngine.getHealthContribution(subaccount, healthType);

        uint256 _spreads = spreads;
        while (_spreads != 0) {
            uint32 _spotId = uint32(_spreads & 0xFF);
            _spreads >>= 8;
            uint32 _perpId = uint32(_spreads & 0xFF);
            _spreads >>= 8;

            IProductEngine.CoreRisk memory perpCoreRisk = perpEngine
                .getCoreRisk(subaccount, _perpId, healthType);

            if (perpCoreRisk.amount == 0) {
                continue;
            }

            IProductEngine.CoreRisk memory spotCoreRisk = spotEngine
                .getCoreRisk(subaccount, _spotId, healthType);

            if (
                (spotCoreRisk.amount == 0) ||
                ((spotCoreRisk.amount > 0) == (perpCoreRisk.amount > 0))
            ) {
                continue;
            }

            int128 basisAmount;
            if (spotCoreRisk.amount > 0) {
                basisAmount = MathHelper.min(
                    spotCoreRisk.amount,
                    -perpCoreRisk.amount
                );
            } else {
                basisAmount = -MathHelper.max(
                    spotCoreRisk.amount,
                    -perpCoreRisk.amount
                );
            }

            // spreads have 5x higher leverage than the underlying products.
            // but it's capped at 100x leverage at most.
            int128 existingWeight = (spotCoreRisk.longWeight +
                perpCoreRisk.longWeight) / 2;
            int128 spreadWeight = RiskHelper._getSpreadWeightX18(
                perpCoreRisk,
                spotCoreRisk,
                healthType
            );

            health += basisAmount
                .mul(spotCoreRisk.price + perpCoreRisk.price)
                .mul(spreadWeight - existingWeight);
            emit PriceQuery(_spotId);
            emit PriceQuery(_perpId);
        }
    }
```

**File:** core/contracts/Clearinghouse.sol (L415-420)
```text
        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
        emit ModifyCollateral(amountRealized, sender, productId);
```
