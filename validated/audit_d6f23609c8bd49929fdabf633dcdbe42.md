### Title
Perp Bad Debt Socialization Attack via Fixed Liquidation Penalty — (`File: core/contracts/ClearinghouseStorage.sol`, `core/contracts/PerpEngine.sol`)

---

### Summary

The Nado perp engine uses a fixed, minimum-bounded liquidation penalty to compute the liquidation price. When a mark price update causes a position's loss to exceed the maintenance margin, the resulting bad debt is socialized across all open-interest holders by adjusting cumulative funding rates. An unprivileged attacker can deliberately create a maximally leveraged long perp position and simultaneously hold a large short position in a separate subaccount. When a sufficiently large price drop occurs, the long is liquidated and its bad debt is socialized — permanently reducing the vQuote balance of all long holders while increasing the vQuote balance of all short holders, including the attacker's short.

---

### Finding Description

**Root cause — fixed liquidation penalty:**

`ClearinghouseStorage.getLiqPriceX18()` computes the liquidation price using a penalty derived from the maintenance weight, floored at `MIN_NON_SPREAD_LIQ_PENALTY_X18`: [1](#0-0) 

The floor is hardcoded at 0.5%: [2](#0-1) 

For a long position of size `S` at mark price `P'`, the liquidation price is `P' * (1 - penalty)`. If the mark price drops by more than the maintenance margin in a single `updatePrice` call, the liquidatee's `vQuoteBalance` remains negative after liquidation — this is bad debt.

**Root cause — bad debt socialization benefits shorts:**

During finalization (`_finalizeSubaccount` with `productId == type(uint32).max`), `PerpEngine.socializeSubaccount()` is called: [3](#0-2) 

Inside `socializeSubaccount`, the bad debt is spread by adjusting cumulative funding rates: [4](#0-3) 

`cumulativeFundingLongX18` is increased (longs lose) and `cumulativeFundingShortX18` is decreased (shorts gain). This is applied to all existing positions via `_updateBalance`: [5](#0-4) 

For a short position (`amount < 0`), a decrease in `cumulativeFundingShortX18` produces a positive `deltaQuote`, directly increasing the short's `vQuoteBalance`.

**Price update path (the trigger):**

The sequencer calls `Clearinghouse.updatePrice()`, which writes the new price into the engine's risk store: [6](#0-5) [7](#0-6) 

This price is immediately used for health checks and liquidation price computation. There is no TWAP or delay.

---

### Impact Explanation

An attacker holding a large short perp position (subaccount C) and a maximally leveraged long perp position (subaccount A) profits as follows when a large price drop occurs:

- Subaccount A is liquidated. Its `vQuoteBalance` after liquidation = `S * (P_liq - P_entry)`, which is deeply negative when `P_entry >> P'`.
- During finalization, `socializeSubaccount` distributes this bad debt across all open interest. Subaccount C's `vQuoteBalance` increases by `bad_debt * |C_short| / openInterest`.
- The attacker's net gain = C's socialization gain − A's initial collateral (which is minimal at maximum leverage).

**Numerical example:**
- `P_entry = 100`, `P' = 80` (20% drop), `longWeightInitialX18 = 0.9`, `longWeightMaintenanceX18 = 0.95`
- `penalty = (0.95 − 1) / 5 = −0.01` → `P_liq = 80 * 0.99 = 79.2`
- `S = 1000`, `C_A = 1000 * 100 * 0.1 = 10,000`
- `bad_debt = 1000 * (100 − 79.2) = 20,800`
- If attacker's short = 50% of OI: C's gain = `20,800 * 0.5 = 10,400`
- Net attacker profit = `10,400 − 10,000 = 400` (plus liquidator discount profit)

The bad debt of 20,800 is a permanent loss for all other long perp holders.

---

### Likelihood Explanation

**Medium.** The attack requires:
1. A mark price drop large enough to exceed the maintenance margin in a single `updatePrice` call — common for volatile crypto perp products.
2. The attacker to hold a short position before the drop — a normal, permissionless action.
3. The attacker to open a maximally leveraged long position — also permissionless.

No sequencer compromise, admin access, or governance capture is required. The attacker uses only standard protocol entry points (`depositCollateral`, signed orders via `OffchainExchange`, and `liquidateSubaccount`). The `require(txn.sender != txn.liquidatee, ERR_UNAUTHORIZED)` check in `ClearinghouseLiq.liquidateSubaccountImpl` is bypassed by using two separate subaccounts. [8](#0-7) 

---

### Recommendation

1. **Introduce a dynamic liquidation penalty** that scales with recent price volatility, rather than relying solely on the fixed maintenance weight formula. The minimum floor of 0.5% (`MIN_NON_SPREAD_LIQ_PENALTY_X18`) is insufficient for assets with high intra-update volatility.

2. **Cap single-update price moves** in `Clearinghouse.updatePrice()` to a maximum percentage change per update interval, analogous to the `MAX_DAILY_FUNDING_RATE` cap already applied in `PerpEngineState.updateStates()`.

3. **Monitor and flag markets** where the maintenance weight implies a liquidation penalty smaller than the observed maximum single-update price move, and consider pausing new position opens in such markets.

---

### Proof of Concept

**Setup:**
- Perp product with `longWeightInitialX18 = 0.9`, `longWeightMaintenanceX18 = 0.95`, mark price `P = 100`.
- Attacker controls subaccounts A (long), B (liquidator), C (short).

**Step 1 — tx1:** Subaccount C opens a large short perp position of size `S_short` at price 100. This is a normal signed order routed through `OffchainExchange`.

**Step 2 — tx2:** Subaccount A opens a maximally leveraged long perp position of size `S = 1000` at price 100. Initial collateral = `1000 * 100 * (1 − 0.9) = 10,000`. Initial health ≈ 0.

**Step 3 — tx3 (sequencer):** Sequencer submits `UpdatePrice` transaction setting mark price to 80 (20% drop). `Clearinghouse.updatePrice()` → `BaseEngine.updatePrice()` writes `priceX18 = 80`.

**Step 4 — tx4:** Subaccount A's maintenance health = `10,000 + 1000 * 80 * 0.95 − 1000 * 100 = 10,000 + 76,000 − 100,000 = −14,000 < 0`. Subaccount B calls `liquidateSubaccount` on A for the perp product. Liquidation price = `80 * 0.99 = 79.2`. A's vQuote after liquidation = `−100,000 + 79,200 = −20,800`.

**Step 5 — tx5:** Subaccount B calls `liquidateSubaccount` on A with `productId = type(uint32).max` (finalization). `_finalizeSubaccount` → `perpEngine.socializeSubaccount(A, insurance)`. Insurance covers partial amount; remaining bad debt ≈ 20,800 is socialized. `cumulativeFundingShortX18` decreases by `20,800 / openInterest`.

**Step 6:** Subaccount C's `vQuoteBalance` increases by `20,800 * S

### Citations

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

**File:** core/contracts/common/Constants.sol (L56-58)
```text
int128 constant MIN_SPREAD_LIQ_PENALTY_X18 = ONE / 400; // 0.25%

int128 constant MIN_NON_SPREAD_LIQ_PENALTY_X18 = ONE / 200; // 0.5%
```

**File:** core/contracts/ClearinghouseLiq.sol (L386-389)
```text
        v.insurance = perpEngine.socializeSubaccount(
            txn.liquidatee,
            v.insurance
        );
```

**File:** core/contracts/ClearinghouseLiq.sol (L601-603)
```text
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);
        require(txn.sender != txn.liquidatee, ERR_UNAUTHORIZED);
        require(isUnderMaintenance(txn.liquidatee), ERR_NOT_LIQUIDATABLE);
```

**File:** core/contracts/PerpEngine.sol (L164-171)
```text
                if (balance.vQuoteBalance < 0) {
                    // socialize across all other participants
                    int128 fundingPerShare = -balance.vQuoteBalance.div(
                        state.openInterest
                    );
                    state.cumulativeFundingLongX18 += fundingPerShare;
                    state.cumulativeFundingShortX18 -= fundingPerShare;
                    balance.vQuoteBalance = 0;
```

**File:** core/contracts/PerpEngineState.sol (L31-36)
```text
        int128 cumulativeFundingAmountX18 = (balance.amount > 0)
            ? state.cumulativeFundingLongX18
            : state.cumulativeFundingShortX18;
        int128 diffX18 = cumulativeFundingAmountX18 -
            balance.lastCumulativeFundingX18;
        int128 deltaQuote = vQuoteDelta - diffX18.mul(balance.amount);
```

**File:** core/contracts/Clearinghouse.sol (L358-375)
```text
    function updatePrice(bytes calldata transaction)
        external
        onlyEndpoint
        returns (uint32, int128)
    {
        IEndpoint.UpdatePrice memory txn = abi.decode(
            transaction[1:],
            (IEndpoint.UpdatePrice)
        );
        require(txn.priceX18 > 0, ERR_INVALID_PRICE);
        IProductEngine engine = productToEngine[txn.productId];
        if (address(engine) != address(0)) {
            engine.updatePrice(txn.productId, txn.priceX18);
            return (txn.productId, txn.priceX18);
        } else {
            return (0, 0);
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
