### Title
`updateRisk` Clobbers Live `priceX18` Field, Corrupting Health Calculations - (File: `core/contracts/BaseEngine.sol`)

---

### Summary

`BaseEngine.updateRisk` overwrites the entire `RiskStore` struct — including the live `priceX18` field that is exclusively managed by the sequencer — without preserving the current on-chain price. The sibling function `_addOrUpdateProduct` explicitly guards against this by reading and restoring `priceX18` before writing the struct. `updateRisk` omits this guard. When an owner legitimately calls `updateRisk` to adjust collateral weights, the live oracle price stored in `_risk().value[productId].priceX18` is silently replaced with whatever stale value was encoded in the calldata, corrupting every subsequent health check for that product.

---

### Finding Description

`priceX18` inside `RiskStore` is the live oracle price used for all health calculations. It is written by the sequencer through `updatePrice`:

```solidity
// BaseEngine.sol line 273-276
function updatePrice(uint32 productId, int128 priceX18) external virtual {
    require(msg.sender == address(_clearinghouse), ERR_UNAUTHORIZED);
    _risk().value[productId].priceX18 = priceX18;
}
```

The protocol already recognises that this field must not be overwritten during a config update. `_addOrUpdateProduct` explicitly preserves it for existing products:

```solidity
// BaseEngine.sol lines 259-262
} else {
    riskStore.priceX18 = _risk().value[productId].priceX18;
}
_risk().value[productId] = riskStore;
```

`updateRisk`, the dedicated owner-callable weight-update path, performs no such preservation:

```solidity
// BaseEngine.sol lines 278-290
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
    _risk().value[productId] = riskStore;   // ← priceX18 clobbered
}
```

The owner must supply a full `RiskStore` including `priceX18`. Because prices are updated continuously by the sequencer, the value encoded in the owner's transaction will be stale by the time it is mined. The write at line 289 replaces the live price with that stale value. The corrupted price then flows directly into every health contribution calculation:

```solidity
// BaseEngine.sol line 174
health += amount.mul(weight).mul(risk.priceX18);
```

`ContractOwner.spotUpdateRisk` and `ContractOwner.perpUpdateRisk` are the public entry points that route to this function, both gated only by `onlyOwner`.

---

### Impact Explanation

`priceX18` is the sole price source for `_calculateProductHealth`. A corrupted value directly scales every user's health contribution for the affected product:

- **Inflated price** (stale price higher than market): all long positions appear over-collateralised. Users can borrow far beyond the true value of their collateral, draining the protocol's liquidity.
- **Deflated price** (stale price lower than market): legitimate positions appear under-collateralised, triggering wrongful liquidations and allowing liquidators to seize collateral at a discount.
- **Zero price** (owner omits the field or provides zero): health contribution from the product collapses to zero, making all long positions appear worthless and all short positions appear infinitely risky.

The corrupted state persists until the sequencer issues the next `UpdatePrice` transaction for that product, giving a window — potentially many blocks — during which any trader can exploit the mispriced health check.

---

### Likelihood Explanation

`updateRisk` is a routine operational function. Adjusting collateral weights in response to market conditions (e.g., tightening `longWeightInitial` for a volatile asset) is a normal governance action. The owner has no in-protocol mechanism to atomically read the current live `priceX18` and include it in the same transaction; the value they encode will always lag the sequencer's most recent `UpdatePrice`. The inconsistency with `_addOrUpdateProduct` — which the codebase already contains as the correct pattern — makes it likely that an operator calling `updateRisk` will not realise they are also overwriting the live price.

---

### Recommendation

Mirror the preservation logic already present in `_addOrUpdateProduct`:

```solidity
function updateRisk(uint32 productId, RiskHelper.RiskStore memory riskStore)
    external
    onlyOwner
{
    require(
        riskStore.longWeightInitial <= riskStore.longWeightMaintenance &&
            riskStore.shortWeightInitial >= riskStore.shortWeightMaintenance,
        ERR_BAD_PRODUCT_CONFIG
    );
    riskStore.priceX18 = _risk().value[productId].priceX18; // preserve live price
    _risk().value[productId] = riskStore;
}
```

---

### Proof of Concept

1. Product `P` has a live `priceX18 = 2000e18` (ETH at $2000), continuously updated by the sequencer.
2. The owner decides to tighten `longWeightInitial` from `0.9e9` to `0.85e9`. They call `ContractOwner.spotUpdateRisk(P, RiskStore{..., priceX18: 1950e18})` — encoding the price they observed a few blocks earlier.
3. The transaction is mined. `_risk().value[P].priceX18` is now `1950e18` instead of `2000e18`.
4. Before the sequencer issues the next `UpdatePrice`, a user with a long ETH position has their health calculated at $1950 instead of $2000. A malicious user who observed the pending `updateRisk` transaction can front-run the next `UpdatePrice` and open a maximally leveraged position while health is computed at the inflated stale price, or — if the stale price is lower — trigger liquidations on healthy accounts.
5. Conversely, if the owner accidentally passes `priceX18 = 0`, every long position in product `P` contributes zero health, making all borrowers against `P` immediately liquidatable. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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

**File:** core/contracts/BaseEngine.sol (L254-262)
```text
        if (isNewProduct) {
            IEndpoint(getEndpoint()).setInitialPrice(
                productId,
                riskStore.priceX18
            );
        } else {
            riskStore.priceX18 = _risk().value[productId].priceX18;
        }
        _risk().value[productId] = riskStore;
```

**File:** core/contracts/BaseEngine.sol (L273-276)
```text
    function updatePrice(uint32 productId, int128 priceX18) external virtual {
        require(msg.sender == address(_clearinghouse), ERR_UNAUTHORIZED);
        _risk().value[productId].priceX18 = priceX18;
    }
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

**File:** core/contracts/ContractOwner.sol (L453-465)
```text
    function spotUpdateRisk(
        uint32 productId,
        RiskHelper.RiskStore memory riskStore
    ) external onlyOwner {
        spotEngine.updateRisk(productId, riskStore);
    }

    function perpUpdateRisk(
        uint32 productId,
        RiskHelper.RiskStore memory riskStore
    ) external onlyOwner {
        perpEngine.updateRisk(productId, riskStore);
    }
```
