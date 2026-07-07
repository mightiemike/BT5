### Title
Delisted Spot Product Borrows Excluded from Health Calculations, Enabling Undercollateralized Borrowing - (File: `core/contracts/BaseEngine.sol`)

---

### Summary

When a spot product is delisted by setting its risk weights to zero via `BaseEngine.updateRisk`, existing borrowers of that product have their outstanding liabilities silently excluded from all health calculations. This allows them to borrow additional collateral against other assets, creating bad debt. Simultaneously, `Clearinghouse.depositCollateral` accepts new deposits of zero-weight (delisted) spot products with no active-product guard.

---

### Finding Description

**Step 1 — The delisting mechanism for spot products**

`delistProduct` in `Clearinghouse.sol` is restricted to perp products only:

```solidity
require(
    productToEngine[txn.productId] == _perpEngine(),
    ERR_INVALID_PRODUCT
);
``` [1](#0-0) 

For spot products, the equivalent "delisting" is performed by the owner calling `BaseEngine.updateRisk` to set all weights to zero. Crucially, `updateRisk` omits the safety constraint present in `_addOrUpdateProduct` that enforces `shortWeightMaintenance >= 10**9` (i.e., ≥ 1.0):

`_addOrUpdateProduct` (enforces floor):
```solidity
require(
    riskStore.longWeightInitial <= riskStore.longWeightMaintenance &&
        riskStore.longWeightMaintenance <= 10**9 &&
        riskStore.shortWeightInitial >= riskStore.shortWeightMaintenance &&
        riskStore.shortWeightMaintenance >= 10**9,   // ← floor enforced
    ERR_BAD_PRODUCT_CONFIG
);
``` [2](#0-1) 

`updateRisk` (no floor):
```solidity
require(
    riskStore.longWeightInitial <= riskStore.longWeightMaintenance &&
        riskStore.shortWeightInitial >= riskStore.shortWeightMaintenance,
    ERR_BAD_PRODUCT_CONFIG
);
_risk().value[productId] = riskStore;
``` [3](#0-2) 

Setting all four weights to zero is therefore valid through `updateRisk`. The protocol itself treats `longWeightInitialX18 == 0` as the canonical "delisted" marker — `_finalizeSubaccount` in `ClearinghouseLiq.sol` explicitly skips zero-weight spot products during liquidation finalization:

```solidity
if (spotEngine.getRisk(spotId).longWeightInitialX18 == 0) {
    continue;
}
``` [4](#0-3) 

**Step 2 — Health calculation with zero weights**

`_getWeightX18` in `RiskHelper.sol` returns the stored weight directly, with no floor and no special handling for zero:

```solidity
if (amount >= 0) {
    weight = healthType == IProductEngine.HealthType.INITIAL
        ? risk.longWeightInitialX18
        : risk.longWeightMaintenanceX18;
} else {
    weight = healthType == IProductEngine.HealthType.INITIAL
        ? risk.shortWeightInitialX18
        : risk.shortWeightMaintenanceX18;
}
return weight;
``` [5](#0-4) 

`_calculateProductHealth` in `BaseEngine.sol` then computes:

```solidity
int128 weight = RiskHelper._getWeightX18(risk, amount, healthType);
health += quoteAmount;

if (amount != 0) {
    if (weight == 2 * ONE) {
        return -INF;
    }
    health += amount.mul(weight).mul(risk.priceX18);
}
``` [6](#0-5) 

The only special-case guard is `weight == 2 * ONE` (a sentinel for products entirely outside the risk system). A weight of **zero** is not caught. For a negative `amount` (a borrow), `amount.mul(0).mul(price) = 0`, so the liability contributes **nothing** to health. The borrow is invisible to both `INITIAL` and `MAINTENANCE` health checks.

**Step 3 — `depositCollateral` accepts zero-weight products**

`Clearinghouse.depositCollateral` has no check that the target product is active or has non-zero weights. Its only guard is that the token address is non-zero (via `_decimals`):

```solidity
function depositCollateral(IEndpoint.DepositCollateral calldata txn)
    external virtual onlyEndpoint
{
    require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);
    require(txn.amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
    ISpotEngine spotEngine = _spotEngine();
    uint8 decimals = _decimals(txn.productId);   // reverts only if token == address(0)
    ...
    spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
}
``` [7](#0-6) 

Since `updateRisk` does not clear the token config, the token address remains non-zero after delisting, and deposits succeed. The deposited amount contributes zero collateral value (weight = 0), but the deposit is recorded in `totalDepositsNormalized`, distorting the utilization ratio used for interest rate calculations.

---

### Impact Explanation

After a spot product is delisted (weights set to zero):

1. **Existing borrowers' liabilities vanish from health.** Any subaccount with a negative balance (borrow) of the delisted product has that debt contribute `0` to both `INITIAL` and `MAINTENANCE` health. The subaccount's apparent health improves, allowing it to borrow more against remaining collateral. This creates bad debt that the insurance fund or depositors must absorb.

2. **New deposits of the delisted product are accepted.** Users can still call `depositCollateral` for the zero-weight product. While the deposit provides no collateral value, it inflates `totalDepositsNormalized`, artificially lowering the utilization ratio and suppressing interest rates for that product's lending pool.

3. **Finalization skips zero-weight borrow positions.** `_finalizeSubaccount` skips all zero-weight spot products unconditionally, meaning a subaccount with an outstanding borrow of a delisted product can be fully finalized without repaying that debt.

---

### Likelihood Explanation

Spot product delisting is a realistic and expected protocol operation (e.g., removing a token due to a security incident, low liquidity, or regulatory reasons). The owner calling `updateRisk` with zero weights is the only available mechanism for spot delisting. Any subaccount that held a borrow position before the delisting can immediately exploit the zero-liability health state — no additional user action is required beyond submitting a new borrow transaction through `Endpoint`.

---

### Recommendation

Mirror the fix recommended in the Sentiment report: split health calculations into two categories.

1. **Post-action health (INITIAL):** In `_calculateProductHealth`, when `healthType` is `INITIAL` or `MAINTENANCE` and the product's `longWeightInitialX18 == 0` (delisted), treat the position as contributing **zero collateral value** for positive balances but **full liability** (`weight = ONE`) for negative balances. This prevents new borrowing against delisted collateral while not penalizing depositors.

2. **Liquidation health (MAINTENANCE):** Continue counting all positions (including delisted ones) at their full liability weight so that existing borrowers of delisted products remain liquidatable.

3. **`depositCollateral` guard:** Add a check that rejects deposits for products whose `longWeightInitialX18 == 0`, consistent with the existing behavior of `_finalizeSubaccount`.

---

### Proof of Concept

1. Spot product `P` (e.g., productId = 3) is live with normal weights. Subaccount `A` borrows 1000 units of `P` (balance = -1000, shortWeightInitial = 1.1e18).
2. Owner calls `BaseEngine.updateRisk(3, RiskStore{longWeightInitial: 0, shortWeightInitial: 0, longWeightMaintenance: 0, shortWeightMaintenance: 0, priceX18: ...})`.
3. `_getWeightX18` now returns `0` for product `P` for any amount and any health type.
4. `_calculateProductHealth` computes `(-1000).mul(0).mul(price) = 0` — the 1000-unit borrow is invisible.
5. Subaccount `A` calls `Endpoint.submitTransactionsChecked` with a `WithdrawCollateral` or new borrow transaction. `getHealth` returns a value as if the 1000-unit debt does not exist.
6. The health check passes; `A` withdraws or borrows additional collateral that would have been blocked if the liability were counted.
7. Protocol is left with unrecoverable bad debt equal to the value of the delisted borrow position. [8](#0-7) [9](#0-8) [7](#0-6) [10](#0-9)

### Citations

**File:** core/contracts/Clearinghouse.sol (L193-209)
```text
    function depositCollateral(IEndpoint.DepositCollateral calldata txn)
        external
        virtual
        onlyEndpoint
    {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);
        require(txn.amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        ISpotEngine spotEngine = _spotEngine();
        uint8 decimals = _decimals(txn.productId);

        require(decimals <= MAX_DECIMALS);
        int256 multiplier = int256(10**(MAX_DECIMALS - decimals));
        int128 amountRealized = int128(txn.amount) * int128(multiplier);

        spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
        emit ModifyCollateral(amountRealized, txn.sender, txn.productId);
    }
```

**File:** core/contracts/Clearinghouse.sol (L299-303)
```text
        // only perp can be delisted
        require(
            productToEngine[txn.productId] == _perpEngine(),
            ERR_INVALID_PRODUCT
        );
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

**File:** core/contracts/BaseEngine.sol (L235-242)
```text
        require(
            riskStore.longWeightInitial <= riskStore.longWeightMaintenance &&
                riskStore.longWeightMaintenance <= 10**9 &&
                riskStore.shortWeightInitial >=
                riskStore.shortWeightMaintenance &&
                riskStore.shortWeightMaintenance >= 10**9,
            ERR_BAD_PRODUCT_CONFIG
        );
```

**File:** core/contracts/BaseEngine.sol (L278-290)
```text
    function updateRisk(uint32 productId, RiskHelper.RiskStore memory riskStore)
        external
        onlyOwner
    {
        require(
            riskStore.longWeightInitial <= riskStore.longWeightMaintenance &&
                riskStore.shortWeightInitial >=
                riskStore.shortWeightMaintenance,
            ERR_BAD_PRODUCT_CONFIG
        );

        _risk().value[productId] = riskStore;
    }
```

**File:** core/contracts/ClearinghouseLiq.sol (L303-305)
```text
            if (spotEngine.getRisk(spotId).longWeightInitialX18 == 0) {
                continue;
            }
```

**File:** core/contracts/libraries/RiskHelper.sol (L34-55)
```text
    function _getWeightX18(
        Risk memory risk,
        int128 amount,
        IProductEngine.HealthType healthType
    ) internal pure returns (int128) {
        if (healthType == IProductEngine.HealthType.PNL) {
            return ONE;
        }

        int128 weight;
        if (amount >= 0) {
            weight = healthType == IProductEngine.HealthType.INITIAL
                ? risk.longWeightInitialX18
                : risk.longWeightMaintenanceX18;
        } else {
            weight = healthType == IProductEngine.HealthType.INITIAL
                ? risk.shortWeightInitialX18
                : risk.shortWeightMaintenanceX18;
        }

        return weight;
    }
```
