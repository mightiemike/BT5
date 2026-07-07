### Title
Flat `LIQUIDATION_FEE` Creates Incentive Misalignment for Small Positions, Leaving Bad Debt Unliquidated — (`core/contracts/EndpointTx.sol`, `core/contracts/common/Constants.sol`)

---

### Summary

Every liquidation in Nado charges the liquidator a flat `LIQUIDATION_FEE = $1` regardless of position size, while the liquidator's actual reward scales proportionally with the notional value of the position being liquidated. For small underwater positions, the flat fee exceeds the liquidation reward, making liquidation economically irrational. These positions will not be liquidated by rational actors, accumulating as bad debt and threatening protocol solvency.

---

### Finding Description

In `EndpointTx.sol`, every non-finalization liquidation unconditionally charges the liquidator a flat `LIQUIDATION_FEE`:

```solidity
if (signedTx.tx.productId != type(uint32).max) {
    chargeFee(signedTx.tx.sender, LIQUIDATION_FEE);
}
``` [1](#0-0) 

`chargeFee` deducts this directly from the liquidator's quote balance:

```solidity
function chargeFee(bytes32 sender, int128 fee, uint32 productId) internal {
    spotEngine.updateBalance(productId, sender, -fee);
    sequencerFee[productId] += fee;
}
``` [2](#0-1) 

The flat fee is hardcoded at `$1`:

```solidity
int128 constant LIQUIDATION_FEE = 1e18; // $1
``` [3](#0-2) 

The liquidator's actual reward comes from buying the position at a discount to oracle price. In `_handleLiquidationPayment`, the liquidation price is computed with a minimum penalty floor of `MIN_NON_SPREAD_LIQ_PENALTY_X18 = 0.5%` for non-spread positions:

```solidity
if (penaltyX18.abs() < MIN_NON_SPREAD_LIQ_PENALTY_X18) {
    penaltyX18 = -MIN_NON_SPREAD_LIQ_PENALTY_X18;
}
``` [4](#0-3) 

Of that discount, `LIQUIDATION_FEE_FRACTION = 50%` goes to the insurance fund, leaving the liquidator with the other 50%:

```solidity
v.liquidationFees = (v.oraclePriceX18 - v.liquidationPriceX18)
    .mul(LIQUIDATION_FEE_FRACTION)
    .mul(txn.amount);
``` [5](#0-4) 

The liquidator's net profit from a position of notional value `P` at minimum penalty is therefore:

```
gross_profit = P × 0.5% × 50% = P × 0.0025
net_profit   = P × 0.0025 − $1 (flat fee)
break_even   = P = $400
```

For spread positions the minimum penalty is `MIN_SPREAD_LIQ_PENALTY_X18 = 0.25%`, pushing the break-even to `P = $800`. [6](#0-5) 

Any position with notional value below these thresholds is unprofitable to liquidate. The protocol has no fallback mechanism to force liquidation of such positions; `N_ACCOUNT` bypasses the fee but is a privileged sequencer-controlled account, not a permissionless actor. [7](#0-6) 

---

### Impact Explanation

Small underwater positions (notional value < ~$400 for non-spread, < ~$800 for spread at minimum penalty) will not be liquidated by rational liquidators because the flat `$1` fee exceeds the liquidation reward. These positions accumulate as bad debt in the protocol. If enough such positions exist simultaneously — which is realistic given the `$0.1` minimum deposit and `$5` minimum first deposit — the insurance fund is depleted without being replenished, and socialization of losses is triggered, directly harming solvent counterparties. [8](#0-7) 

---

### Likelihood Explanation

The minimum deposit is `$0.1` and the minimum first deposit is `$5`. Positions in this range are well below the $400 break-even threshold. During periods of high volatility, many small accounts can simultaneously fall below maintenance margin. Since the liquidation reward for a $5 position at minimum penalty is only `$5 × 0.0025 = $0.0125` — 80× less than the flat $1 fee — no rational liquidator will act. The `N_ACCOUNT` sequencer path bypasses the fee but is not a permissionless, incentive-driven actor, so it cannot be relied upon to fill this gap.

---

### Recommendation

Replace the flat `LIQUIDATION_FEE` with a fee that scales with the notional value of the liquidated position, analogous to the PoolTogether fix of making `maxFee` a function of the tier's prize size rather than the minimum prize. For example:

```solidity
// Instead of:
chargeFee(signedTx.tx.sender, LIQUIDATION_FEE);

// Use a fraction of the position's notional value:
int128 scaledFee = notionalValue.mul(LIQUIDATION_FEE_RATE);
chargeFee(signedTx.tx.sender, MathHelper.max(scaledFee, MIN_LIQUIDATION_FEE));
```

This ensures the fee is always smaller than the liquidation reward, preserving the incentive to liquidate positions of any size.

---

### Proof of Concept

1. Alice deposits `$5` (minimum first deposit) and opens a small perp position.
2. Market moves against Alice; her maintenance health drops below zero.
3. A liquidator evaluates the liquidation:
   - Position notional value: `$5`
   - Minimum penalty: `0.5%` → discount = `$0.025`
   - Liquidator's share (50%): `$0.0125`
   - Flat `LIQUIDATION_FEE` charged: `$1.00`
   - Net profit: `$0.0125 − $1.00 = −$0.9875` (loss)
4. No rational liquidator submits the transaction.
5. Alice's position remains underwater indefinitely, accumulating bad debt.
6. If many such positions exist simultaneously, the insurance fund is drained without replenishment, triggering socialization of losses against solvent users. [1](#0-0) [9](#0-8) [10](#0-9) [11](#0-10)

### Citations

**File:** core/contracts/EndpointTx.sol (L134-141)
```text
    function chargeFee(
        bytes32 sender,
        int128 fee,
        uint32 productId
    ) internal {
        spotEngine.updateBalance(productId, sender, -fee);
        sequencerFee[productId] += fee;
    }
```

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

**File:** core/contracts/common/Constants.sol (L27-28)
```text
int128 constant LIQUIDATION_FEE = 1e18; // $1
int128 constant HEALTHCHECK_FEE = 1e18; // $1
```

**File:** core/contracts/common/Constants.sol (L40-42)
```text
int256 constant MIN_DEPOSIT_AMOUNT = ONE / 10; // $0.1

int256 constant MIN_FIRST_DEPOSIT_AMOUNT = 5 * ONE; // $5
```

**File:** core/contracts/common/Constants.sol (L56-58)
```text
int128 constant MIN_SPREAD_LIQ_PENALTY_X18 = ONE / 400; // 0.25%

int128 constant MIN_NON_SPREAD_LIQ_PENALTY_X18 = ONE / 200; // 0.5%
```

**File:** core/contracts/ClearinghouseStorage.sol (L47-59)
```text
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

**File:** core/contracts/ClearinghouseLiq.sol (L549-569)
```text
            v.liquidationPayment = v.liquidationPriceX18.mul(txn.amount);
            v.liquidationFees = (v.oraclePriceX18 - v.liquidationPriceX18)
                .mul(LIQUIDATION_FEE_FRACTION)
                .mul(txn.amount);
            perpEngine.updateBalance(
                txn.productId,
                txn.liquidatee,
                -txn.amount,
                v.liquidationPayment
            );
            perpEngine.updateBalance(
                txn.productId,
                txn.sender,
                txn.amount,
                -v.liquidationPayment
            );
            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                txn.sender,
                -v.liquidationFees
            );
```
