### Title
Quote Asset Price Permanently Hardcoded to `ONE`, Ignoring Stablecoin Depeg — (`File: core/contracts/SpotEngine.sol`, `core/contracts/Clearinghouse.sol`, `core/contracts/Endpoint.sol`)

---

### Summary

The Nado protocol permanently hardcodes the price of the quote asset (`QUOTE_PRODUCT_ID = 0`, the system's stablecoin collateral) to `ONE` (10^18 = $1.00) in at least three independent locations. No mechanism exists to update this price in response to market conditions. If the quote stablecoin depegs, the system continues to value it at $1, causing health scores to be inflated, liquidations to be suppressed, and `checkMinDeposit` to accept undercollateralized deposits.

---

### Finding Description

**Root cause 1 — `Endpoint.sol` initialization:**

During `initialize`, the Endpoint sets:

```solidity
priceX18[QUOTE_PRODUCT_ID] = ONE;
``` [1](#0-0) 

`setInitialPrice` enforces `require(priceX18[productId] == 0, ERR_UNAUTHORIZED)`, so it can never be called again for `QUOTE_PRODUCT_ID` after initialization. [2](#0-1) 

**Root cause 2 — `SpotEngine.sol` risk store initialization:**

The `SpotEngine.initialize` function hardcodes the quote product's risk store price:

```solidity
_risk().value[QUOTE_PRODUCT_ID] = RiskHelper.RiskStore({
    ...
    priceX18: ONE
});
``` [3](#0-2) 

This `priceX18` field is what `_calculateProductHealth` reads when computing health contributions for every subaccount holding quote balance.

**Root cause 3 — `Clearinghouse.checkMinDeposit` explicit bypass:**

Even if the risk store price were somehow updated, `checkMinDeposit` unconditionally overrides it:

```solidity
int128 priceX18 = ONE;
if (productId != QUOTE_PRODUCT_ID) {
    priceX18 = _getPriceX18(productId);
}
``` [4](#0-3) 

**Health calculation path:**

`BaseEngine._calculateProductHealth` computes:

```solidity
health += amount.mul(weight).mul(risk.priceX18);
``` [5](#0-4) 

For `QUOTE_PRODUCT_ID`, `risk.priceX18` is always `ONE`. With `longWeightInitial = shortWeightInitial = 1e9` (= `ONE`), 100 USDC always contributes exactly 100 to health, regardless of market price. [3](#0-2) 

---

### Impact Explanation

If the quote stablecoin (e.g., USDC) depegs to $0.80:

1. **`checkMinDeposit` accepts undercollateralized deposits**: A user depositing 100 USDC (real value $80) passes the minimum deposit check as if it were worth $100.
2. **Health scores are inflated**: `getHealth` returns a value 25% higher than the true USD value of quote collateral. Positions that are actually undercollateralized appear healthy.
3. **Liquidations are suppressed**: `ClearinghouseLiq` relies on `getHealth` returning a negative value to trigger liquidation. With inflated quote prices, insolvent subaccounts are never liquidated.
4. **Protocol insolvency**: The insurance fund and other depositors absorb losses that should have been liquidated earlier. [6](#0-5) 

---

### Likelihood Explanation

Stablecoin depegs are historically documented (USDC briefly depegged to ~$0.87 in March 2023; UST collapsed entirely). The trigger requires no privileged access — any user depositing or trading with quote collateral during a depeg event exercises the broken path. The hardcode is unconditional and cannot be overridden by the sequencer or owner without a contract upgrade.

---

### Recommendation

- Remove the hardcoded `priceX18[QUOTE_PRODUCT_ID] = ONE` from `Endpoint.initialize` and allow the sequencer to submit `UpdatePrice` transactions for `QUOTE_PRODUCT_ID`.
- Remove the `if (productId != QUOTE_PRODUCT_ID)` branch in `checkMinDeposit` so the quote asset price is fetched from the oracle like all other assets.
- Initialize the quote risk store with a sentinel value and require an explicit price update before the system accepts deposits.

---

### Proof of Concept

1. USDC depegs to $0.80.
2. Attacker deposits 1,000,000 USDC (real value: $800,000).
3. `checkMinDeposit` evaluates `priceX18 = ONE`, passes the check treating the deposit as $1,000,000.
4. Attacker opens a maximum-leverage long position on a perp product. `getHealth` computes quote contribution as 1,000,000 × 1 × 1 = 1,000,000 (inflated by $200,000).
5. The position is undercollateralized in real terms but passes the initial health check.
6. USDC continues to fall. `getHealth` never goes negative because the quote price remains hardcoded at $1. No liquidation is triggered.
7. Attacker defaults; the protocol absorbs the $200,000+ shortfall from the insurance fund or socializes losses to depositors. [1](#0-0) [3](#0-2) [7](#0-6)

### Citations

**File:** core/contracts/Endpoint.sol (L54-54)
```text
        priceX18[QUOTE_PRODUCT_ID] = ONE;
```

**File:** core/contracts/Endpoint.sol (L314-324)
```text
    function setInitialPrice(uint32 productId, int128 initialPriceX18)
        external
    {
        require(
            msg.sender == address(spotEngine) ||
                msg.sender == address(perpEngine),
            ERR_UNAUTHORIZED
        );
        require(priceX18[productId] == 0, ERR_UNAUTHORIZED);
        priceX18[productId] = initialPriceX18;
    }
```

**File:** core/contracts/SpotEngine.sol (L32-38)
```text
        _risk().value[QUOTE_PRODUCT_ID] = RiskHelper.RiskStore({
            longWeightInitial: 1e9,
            shortWeightInitial: 1e9,
            longWeightMaintenance: 1e9,
            shortWeightMaintenance: 1e9,
            priceX18: ONE
        });
```

**File:** core/contracts/Clearinghouse.sol (L71-84)
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
```

**File:** core/contracts/Clearinghouse.sol (L709-714)
```text
        int128 priceX18 = ONE;
        if (productId != QUOTE_PRODUCT_ID) {
            priceX18 = _getPriceX18(productId);
        }

        return priceX18.mul(amountRealized) >= minDepositAmount;
```

**File:** core/contracts/BaseEngine.sol (L174-174)
```text
            health += amount.mul(weight).mul(risk.priceX18);
```
