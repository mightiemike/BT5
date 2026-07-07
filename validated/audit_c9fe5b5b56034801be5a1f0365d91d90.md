### Title
Flat `LIQUIDATION_FEE` Makes Small Positions Unprofitable to Liquidate, Enabling Bad Debt Accumulation — (`core/contracts/EndpointTx.sol`, `core/contracts/common/Constants.sol`)

---

### Summary

Every liquidation in Nado charges the liquidator a flat `LIQUIDATION_FEE = $1` from their subaccount balance. The liquidator's profit from the price discount is purely percentage-based (minimum 0.5% of position value, of which only 50% is kept by the liquidator). For any position worth less than ~$400, the flat fee exceeds the profit, making liquidation economically irrational. Since the minimum first deposit is only $5, a large range of undercollateralized positions will never be liquidated by rational actors, accumulating bad debt that can drain the insurance fund and threaten protocol solvency.

---

### Finding Description

In `EndpointTx.sol`, every non-finalization `LiquidateSubaccount` transaction charges the liquidator a flat fee:

```solidity
// EndpointTx.sol line 408-410
if (signedTx.tx.productId != type(uint32).max) {
    chargeFee(signedTx.tx.sender, LIQUIDATION_FEE);
}
```

`LIQUIDATION_FEE = 1e18` ($1) is a fixed cost deducted from the liquidator's subaccount balance regardless of position size.

The liquidator's gross profit comes from the price discount in `_handleLiquidationPayment` (`ClearinghouseLiq.sol`). For a non-spread position:

```
liquidationFees = (oraclePriceX18 - liquidationPriceX18) * LIQUIDATION_FEE_FRACTION * amount
```

The liquidator pays `liquidationPayment + liquidationFees` and receives the asset at `liquidationPriceX18`. The `liquidationFees` go entirely to the insurance fund (`insurance += v.liquidationFees`). The liquidator's net gain from the discount is:

```
liquidator_profit = oraclePriceX18 * |penaltyX18| * amount * (1 - LIQUIDATION_FEE_FRACTION)
                  = position_value * penalty * 0.5
```

The minimum penalty is enforced in `ClearinghouseStorage.sol`:

```solidity
// ClearinghouseStorage.sol line 52-57
if (penaltyX18.abs() < MIN_NON_SPREAD_LIQ_PENALTY_X18) {
    penaltyX18 = -MIN_NON_SPREAD_LIQ_PENALTY_X18; // 0.5%
}
```

At minimum penalty (0.5%), the liquidator's profit is:

```
profit = position_value * 0.005 * 0.5 = position_value * 0.0025
```

Break-even against the $1 flat fee:

```
position_value * 0.0025 = $1  →  position_value = $400
```

For spread positions (`MIN_SPREAD_LIQ_PENALTY_X18 = 0.25%`), the break-even is $800.

Meanwhile, the minimum first deposit is `MIN_FIRST_DEPOSIT_AMOUNT = $5`, and subsequent deposits can be as small as `MIN_DEPOSIT_AMOUNT = $0.1`. This creates a large range of positions ($5–$400) that are economically irrational to liquidate.

**Concrete example:**
- User deposits $10 of collateral and opens a leveraged perp position.
- Price moves against them; maintenance health drops below zero.
- Liquidator calculates: profit = $10 * 0.5% * 50% = $0.025, but must pay $1 flat fee → net loss of $0.975.
- No rational liquidator submits the transaction.
- The position remains underwater, accumulating bad debt against the insurance fund.

---

### Impact Explanation

Undercollateralized positions below ~$400 (non-spread) or ~$800 (spread) will not be liquidated by rational actors. These positions represent bad debt that is absorbed by the insurance fund. If the insurance fund is depleted, the protocol enters socialization mode (`socializeSubaccount`), where losses are spread across all counterparties, directly corrupting the quote balances of innocent users. At scale — especially during volatile market conditions when many small positions go underwater simultaneously — this can render the protocol insolvent.

---

### Likelihood Explanation

The minimum first deposit is $5 and subsequent deposits are $0.1. Any user who deposits a small amount and opens a leveraged position creates an unliquidatable account. This is trivially reachable by any unprivileged user with no special knowledge. During market stress (rapid price drops), many such positions will simultaneously breach maintenance health, and the flat $1 fee ensures none of them are liquidated. A sophisticated attacker could deliberately open hundreds of small positions near the liquidation threshold to grief the protocol, knowing the insurance fund will absorb the losses.

---

### Recommendation

1. **Raise `MIN_FIRST_DEPOSIT_AMOUNT`** to a value that ensures the liquidation profit at minimum penalty exceeds the flat fee. Given `LIQUIDATION_FEE = $1`, `LIQUIDATION_FEE_FRACTION = 50%`, and `MIN_NON_SPREAD_LIQ_PENALTY_X18 = 0.5%`, the minimum deposit should be at least $400–$500 to ensure profitability.
2. **Alternatively, replace the flat `LIQUIDATION_FEE` with a percentage-based fee** proportional to position value, so the fee scales with the liquidation reward.
3. **Or reduce `LIQUIDATION_FEE_FRACTION`** so liquidators retain a larger share of the discount, lowering the break-even position size.

---

### Proof of Concept

1. User A calls `depositCollateral` with $10 USDC (above `MIN_FIRST_DEPOSIT_AMOUNT = $5`).
2. User A opens a leveraged perp position via `MatchOrders`.
3. Oracle price moves adversely; `isUnderMaintenance(userA)` returns `true`.
4. Liquidator Bot calculates expected profit:
   - Position value ≈ $10
   - Gross profit = $10 × 0.5% × 50% = **$0.025**
   - Flat fee charged = **$1.00** (`LIQUIDATION_FEE`)
   - Net = **−$0.975**
5. No rational liquidator submits `LiquidateSubaccount` for User A.
6. User A's position remains underwater indefinitely.
7. Repeat with 1,000 such accounts → $10,000 in bad debt with zero liquidation incentive.
8. Insurance fund absorbs losses; if depleted, `socializeSubaccount` corrupts all counterparty balances.

**Relevant constants:** [1](#0-0) [2](#0-1) [3](#0-2) 

**Flat fee charged to liquidator:** [4](#0-3) 

**Liquidation fees routed to insurance, not liquidator:** [5](#0-4) 

**Minimum penalty floor:** [6](#0-5)

### Citations

**File:** core/contracts/common/Constants.sol (L27-27)
```text
int128 constant LIQUIDATION_FEE = 1e18; // $1
```

**File:** core/contracts/common/Constants.sol (L36-42)
```text
int128 constant LIQUIDATION_FEE_FRACTION = 500_000_000_000_000_000; // 50%

int128 constant INTEREST_FEE_FRACTION = 200_000_000_000_000_000; // 20%

int256 constant MIN_DEPOSIT_AMOUNT = ONE / 10; // $0.1

int256 constant MIN_FIRST_DEPOSIT_AMOUNT = 5 * ONE; // $5
```

**File:** core/contracts/common/Constants.sol (L56-58)
```text
int128 constant MIN_SPREAD_LIQ_PENALTY_X18 = ONE / 400; // 0.25%

int128 constant MIN_NON_SPREAD_LIQ_PENALTY_X18 = ONE / 200; // 0.5%
```

**File:** core/contracts/EndpointTx.sol (L408-410)
```text
                if (signedTx.tx.productId != type(uint32).max) {
                    chargeFee(signedTx.tx.sender, LIQUIDATION_FEE);
                }
```

**File:** core/contracts/ClearinghouseLiq.sol (L579-579)
```text
        insurance += v.liquidationFees;
```

**File:** core/contracts/ClearinghouseStorage.sol (L52-58)
```text
        if (penaltyX18.abs() < MIN_NON_SPREAD_LIQ_PENALTY_X18) {
            if (penaltyX18 < 0) {
                penaltyX18 = -MIN_NON_SPREAD_LIQ_PENALTY_X18;
            } else {
                penaltyX18 = MIN_NON_SPREAD_LIQ_PENALTY_X18;
            }
        }
```
