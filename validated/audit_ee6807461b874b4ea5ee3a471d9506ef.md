### Title
External Liquidators Have No Economic Incentive for Small Positions Due to Fixed `LIQUIDATION_FEE` Exceeding Profit — (File: `core/contracts/EndpointTx.sol`, `core/contracts/common/Constants.sol`, `core/contracts/ClearinghouseStorage.sol`)

---

### Summary

External liquidators are charged a flat `LIQUIDATION_FEE = $1` per liquidation transaction. Their profit is bounded below by `MIN_NON_SPREAD_LIQ_PENALTY_X18 = 0.5%` of position value, of which only 50% (`LIQUIDATION_FEE_FRACTION`) accrues to the liquidator. For positions worth less than ~$400, the fee exceeds the profit, making liquidation economically irrational for any external actor. The protocol's own `N_ACCOUNT` bot is exempt from this fee. If `N_ACCOUNT` fails, small underwater positions are never liquidated and accumulate as bad debt.

---

### Finding Description

In `EndpointTx.sol`, every external liquidation transaction charges the liquidator a flat `LIQUIDATION_FEE`:

```solidity
// EndpointTx.sol:396-410
if (signedTx.tx.sender != N_ACCOUNT) {
    validateSignedTx(...);
    if (signedTx.tx.productId != type(uint32).max) {
        chargeFee(signedTx.tx.sender, LIQUIDATION_FEE);
    }
}
```

`LIQUIDATION_FEE = 1e18` ($1) and `N_ACCOUNT` is fully exempt. [1](#0-0) [2](#0-1) 

The liquidator's profit is derived from the price discount in `_handleLiquidationPayment`. For a spot liquidation:

```
liquidationFees = (oraclePrice - liquidationPrice) * LIQUIDATION_FEE_FRACTION * amount
liquidator_profit = (oraclePrice - liquidationPrice) * amount * (1 - LIQUIDATION_FEE_FRACTION)
                  = price_discount * amount * 0.50
``` [3](#0-2) [4](#0-3) 

The price discount is floored by `getLiqPriceX18` at `MIN_NON_SPREAD_LIQ_PENALTY_X18 = 0.5%` for non-spread positions and `MIN_SPREAD_LIQ_PENALTY_X18 = 0.25%` for spread positions:

```solidity
// ClearinghouseStorage.sol:47-58
int128 penaltyX18 = (RiskHelper._getWeightX18(...MAINTENANCE) - ONE) / 5;
if (penaltyX18.abs() < MIN_NON_SPREAD_LIQ_PENALTY_X18) {
    penaltyX18 = -MIN_NON_SPREAD_LIQ_PENALTY_X18;
}
``` [5](#0-4) [6](#0-5) 

At the minimum penalty, the liquidator's gross profit is `0.5% × 50% = 0.25%` of position value. For this to exceed the $1 flat fee, the position must be worth at least **$400** (non-spread) or **$800** (spread). Any underwater position below these thresholds is economically irrational for an external liquidator to touch.

---

### Impact Explanation

Positions below the profitability threshold will not be liquidated by external actors. If `N_ACCOUNT` (the protocol's own bot) is unavailable, censored, or fails, these positions remain underwater indefinitely. Each such position represents a quote liability that the insurance fund must eventually absorb via `socializeSubaccount`. Accumulation of many small bad-debt positions drains the insurance fund and socializes losses across all counterparties. [7](#0-6) 

---

### Likelihood Explanation

The protocol explicitly bifurcates the liquidation path: `N_ACCOUNT` is exempt from the fee and is the intended liquidator for all positions. External liquidators are a fallback. The comment in `EndpointTx.sol` acknowledges that finalization yields no profit for the liquidator, confirming the design is aware of incentive gaps. Any operational failure of `N_ACCOUNT` — sequencer downtime, key compromise, or bot misconfiguration — leaves the small-position liquidation gap fully exposed. Likelihood is **Medium**: the bot is a single point of failure for an entire class of positions. [8](#0-7) 

---

### Recommendation

1. **Scale the liquidation fee to position size** rather than using a flat $1. For example, charge a percentage of `liquidationFees` instead of a fixed amount, ensuring the fee is always less than the liquidator's profit.
2. **Alternatively, lower or eliminate `LIQUIDATION_FEE`** for positions below a defined notional threshold, accepting the sybil risk is low for small positions.
3. **Enforce a minimum position notional** at the protocol level so no position can fall below the $400 profitability floor.
4. **Add a keeper incentive** funded by the insurance fund for positions that have been underwater for more than a configurable duration without being liquidated.

---

### Proof of Concept

**Setup**: A trader opens a spot position worth $300 in an asset with `longWeightMaintenanceX18 = 0.975e18` (high-quality asset). The penalty is `(0.975 - 1) / 5 = -0.005`, which is exactly at `MIN_NON_SPREAD_LIQ_PENALTY_X18 = 0.5%`.

**Liquidation economics for an external liquidator**:
- Position value: $300
- Price discount: 0.5% → $1.50
- `liquidationFees` to insurance: $1.50 × 50% = $0.75
- Liquidator gross profit: $1.50 × 50% = $0.75
- `LIQUIDATION_FEE` charged: $1.00
- **Net profit: $0.75 − $1.00 = −$0.25** (loss)

**Result**: No rational external liquidator submits this transaction. The position remains underwater. If `N_ACCOUNT` is offline, the bad debt is never cleared. [9](#0-8) [10](#0-9) [11](#0-10) [1](#0-0)

### Citations

**File:** core/contracts/EndpointTx.sol (L396-411)
```text
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
```

**File:** core/contracts/common/Constants.sol (L27-36)
```text
int128 constant LIQUIDATION_FEE = 1e18; // $1
int128 constant HEALTHCHECK_FEE = 1e18; // $1

uint128 constant INT128_MAX = uint128(type(int128).max);

uint64 constant SECONDS_PER_DAY = 3600 * 24;

uint32 constant VRTX_PRODUCT_ID = 41;

int128 constant LIQUIDATION_FEE_FRACTION = 500_000_000_000_000_000; // 50%
```

**File:** core/contracts/common/Constants.sol (L56-58)
```text
int128 constant MIN_SPREAD_LIQ_PENALTY_X18 = ONE / 400; // 0.25%

int128 constant MIN_NON_SPREAD_LIQ_PENALTY_X18 = ONE / 200; // 0.5%
```

**File:** core/contracts/ClearinghouseLiq.sol (L395-412)
```text
        int128 insuranceCover = MathHelper.min(
            v.insurance,
            -quoteBalance.amount
        );
        if (insuranceCover > 0) {
            v.insurance -= insuranceCover;
            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                txn.liquidatee,
                insuranceCover
            );
        }
        if (v.insurance <= 0) {
            spotEngine.socializeSubaccount(txn.liquidatee);
        }
        v.insurance += lastLiquidationFees;
        insurance = v.insurance;
        return true;
```

**File:** core/contracts/ClearinghouseLiq.sol (L507-536)
```text
        } else if (engine == address(spotEngine)) {
            (v.liquidationPriceX18, v.oraclePriceX18) = getLiqPriceX18(
                txn.productId,
                txn.amount
            );

            v.liquidationPayment = v.liquidationPriceX18.mul(txn.amount);
            v.liquidationFees = (v.oraclePriceX18 - v.liquidationPriceX18)
                .mul(LIQUIDATION_FEE_FRACTION)
                .mul(txn.amount);

            spotEngine.updateBalance(
                txn.productId,
                txn.liquidatee,
                -txn.amount
            );

            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                txn.liquidatee,
                v.liquidationPayment
            );

            spotEngine.updateBalance(txn.productId, txn.sender, txn.amount);

            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                txn.sender,
                -v.liquidationPayment - v.liquidationFees
            );
```

**File:** core/contracts/ClearinghouseStorage.sol (L41-59)
```text
    function getLiqPriceX18(uint32 productId, int128 amount)
        internal
        returns (int128, int128)
    {
        RiskHelper.Risk memory risk = IProductEngine(productToEngine[productId])
            .getRisk(productId);
        int128 penaltyX18 = (RiskHelper._getWeightX18(
            risk,
            amount,
            IProductEngine.HealthType.MAINTENANCE
        ) - ONE) / 5;
        if (penaltyX18.abs() < MIN_NON_SPREAD_LIQ_PENALTY_X18) {
            if (penaltyX18 < 0) {
                penaltyX18 = -MIN_NON_SPREAD_LIQ_PENALTY_X18;
            } else {
                penaltyX18 = MIN_NON_SPREAD_LIQ_PENALTY_X18;
            }
        }
        return (risk.priceX18.mul(ONE + penaltyX18), risk.priceX18);
```
