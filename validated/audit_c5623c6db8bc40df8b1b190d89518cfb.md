### Title
Hardcoded USDC Peg at $1 in Quote Product Risk Store Enables Liquidation Bypass During Depeg Events — (`core/contracts/SpotEngine.sol`, `core/contracts/Clearinghouse.sol`)

---

### Summary

The Nado protocol permanently hardcodes the price of the quote token (USDC, `QUOTE_PRODUCT_ID = 0`) to exactly `ONE` ($1) in two distinct places: the risk store initialized in `SpotEngine.initialize`, and an explicit bypass in `Clearinghouse.checkMinDeposit`. Because all subaccount health calculations consume `risk.priceX18` from the stored risk data, and because the quote product's price is initialized to `ONE` with no guaranteed on-chain update path, the protocol permanently values every unit of USDC at exactly $1 when computing collateral health. This mirrors the Isomorph USDC peg assumption exactly: if USDC depegs below $1, collateral is overvalued and insolvent borrowers escape liquidation; if USDC rises above $1, collateral is undervalued and solvent borrowers face unfair liquidation.

---

### Finding Description

**Root cause 1 — `SpotEngine.initialize` hardcodes `priceX18: ONE` for the quote product:**

During initialization, the risk store for `QUOTE_PRODUCT_ID` is set with a permanently hardcoded price:

```solidity
_risk().value[QUOTE_PRODUCT_ID] = RiskHelper.RiskStore({
    longWeightInitial: 1e9,
    shortWeightInitial: 1e9,
    longWeightMaintenance: 1e9,
    shortWeightMaintenance: 1e9,
    priceX18: ONE          // ← hardcoded $1, never oracle-sourced
});
``` [1](#0-0) 

**Root cause 2 — `Clearinghouse.checkMinDeposit` explicitly bypasses oracle price for the quote product:**

```solidity
int128 priceX18 = ONE;
if (productId != QUOTE_PRODUCT_ID) {
    priceX18 = _getPriceX18(productId);
}
return priceX18.mul(amountRealized) >= minDepositAmount;
``` [2](#0-1) 

The branch explicitly skips the oracle call for `QUOTE_PRODUCT_ID`, hardcoding `ONE` regardless of any sequencer-supplied price.

**Root cause 3 — Health calculation consumes `risk.priceX18` directly:**

`BaseEngine._calculateProductHealth` computes every product's health contribution as `amount * weight * risk.priceX18`:

```solidity
health += amount.mul(weight).mul(risk.priceX18);
``` [3](#0-2) 

For the quote product, `risk.priceX18 = ONE` (from initialization above), and the weights are all `1e9` (converting to `ONE` in X18 representation), so the USDC balance contributes exactly `amount * 1 * 1 = amount` to health — permanently pegged at $1 per unit.

**Root cause 4 — `updatePrice` cannot reach the quote product if `productToEngine[0]` is unset:**

The sequencer-driven price update path in `Clearinghouse.updatePrice` gates on `productToEngine[txn.productId] != address(0)`:

```solidity
IProductEngine engine = productToEngine[txn.productId];
if (address(engine) != address(0)) {
    engine.updatePrice(txn.productId, txn.priceX18);
    return (txn.productId, txn.priceX18);
} else {
    return (0, 0);
}
``` [4](#0-3) 

If `productToEngine[QUOTE_PRODUCT_ID]` is not populated (the quote product is a special product initialized separately from the normal `addOrUpdateProduct` flow), the sequencer's `UpdatePrice` transaction silently returns `(0, 0)` without updating the stored price, leaving `risk.priceX18 = ONE` permanently.

The `QUOTE_PRODUCT_ID` constant is defined as:

```solidity
uint32 constant QUOTE_PRODUCT_ID = 0;
int128 constant ONE = 10**18;
``` [5](#0-4) 

---

### Impact Explanation

All subaccount health in Nado is denominated in the quote token (USDC). When `risk.priceX18` for `QUOTE_PRODUCT_ID` is permanently `ONE`:

- **USDC depeg below $1 (e.g., $0.90):** A subaccount holding 10,000 USDC as collateral is valued at $10,000 by the protocol, but its real value is $9,000. The subaccount's health appears higher than it truly is. Borrowers who are genuinely insolvent in real USD terms cannot be liquidated, leaving the protocol holding bad debt.
- **USDC rise above $1 (e.g., $1.05):** The same 10,000 USDC is worth $10,500 in reality but only $10,000 to the protocol. Solvent borrowers are undervalued and become eligible for liquidation they do not deserve, causing unfair asset seizure.

The `checkMinDeposit` hardcoding additionally allows deposits whose real USD value is below the minimum threshold to pass the gate when USDC trades below $1, and incorrectly rejects valid deposits when USDC trades above $1.

---

### Likelihood Explanation

USDC has historically depegged (notably to ~$0.87 during the March 2023 SVB event). The Nado protocol is deployed on Ink Chain and explicitly handles USDC as its quote token. Any USDC depeg event — even a brief one — directly triggers the mispricing. No privileged access is required: any trader who holds USDC collateral and has an open borrow position benefits automatically from the overvaluation during a depeg, and any liquidator is blocked from acting on genuinely insolvent accounts.

---

### Recommendation

1. Introduce a Chainlink USDC/USD price feed and update `risk.priceX18` for `QUOTE_PRODUCT_ID` via the sequencer's `UpdatePrice` path, ensuring `productToEngine[QUOTE_PRODUCT_ID]` is populated so the update is not silently dropped.
2. Remove the explicit `if (productId != QUOTE_PRODUCT_ID)` bypass in `Clearinghouse.checkMinDeposit` and use the oracle-sourced price for all products uniformly.
3. Consider adding a staleness check and a circuit-breaker that halts new borrows if the USDC oracle price deviates beyond a configurable band (e.g., ±2%) from $1.

---

### Proof of Concept

1. Protocol is deployed; `SpotEngine.initialize` sets `risk.priceX18 = ONE` for `QUOTE_PRODUCT_ID`.
2. Attacker deposits 10,000 USDC and borrows the maximum allowed against it (health just above the initial margin threshold).
3. USDC depegs to $0.90 on external markets. Real collateral value = $9,000.
4. Protocol health check calls `_calculateProductHealth(0, attacker, MAINTENANCE)` → returns `10000e18` (uses hardcoded `ONE`), not `9000e18`.
5. Liquidator calls `liquidateSubaccount`; `getHealth(attacker, MAINTENANCE) >= 0` passes because the protocol still sees $10,000 of collateral.
6. Liquidation is blocked. The protocol accumulates bad debt equal to the gap between the hardcoded valuation and the real USDC value multiplied by the total USDC collateral across all affected subaccounts. [1](#0-0) [2](#0-1) [6](#0-5)

### Citations

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

**File:** core/contracts/Clearinghouse.sol (L368-374)
```text
        IProductEngine engine = productToEngine[txn.productId];
        if (address(engine) != address(0)) {
            engine.updatePrice(txn.productId, txn.priceX18);
            return (txn.productId, txn.priceX18);
        } else {
            return (0, 0);
        }
```

**File:** core/contracts/Clearinghouse.sol (L709-714)
```text
        int128 priceX18 = ONE;
        if (productId != QUOTE_PRODUCT_ID) {
            priceX18 = _getPriceX18(productId);
        }

        return priceX18.mul(amountRealized) >= minDepositAmount;
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

**File:** core/contracts/common/Constants.sol (L5-17)
```text
uint32 constant QUOTE_PRODUCT_ID = 0;

/// @dev Fees account
bytes32 constant FEES_ACCOUNT = bytes32(0);
bytes32 constant X_ACCOUNT = 0x0000000000000000000000000000000000000000000000000000000000000001;
bytes32 constant N_ACCOUNT = 0x0000000000000000000000000000000000000000000000000000000000000002;
bytes32 constant NLP_POOL_ACCOUNT_START = 0x0000000000000000000000000000000000000000000000000000000100000000;

string constant DEFAULT_REFERRAL_CODE = "-1";

uint128 constant MINIMUM_LIQUIDITY = 10**3;

int128 constant ONE = 10**18;
```
