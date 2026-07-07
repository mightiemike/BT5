### Title
`productId == 0` Sentinel Overloading in `updatePrice` Silently Skips `Endpoint.priceX18[0]` Update — (`core/contracts/EndpointTx.sol` / `core/contracts/Clearinghouse.sol`)

---

### Summary

`Clearinghouse.updatePrice` uses the return value `(0, 0)` as a sentinel to signal "no engine registered for this product." `EndpointTx.sol` then guards the `Endpoint.priceX18` write with `if (productId != 0)`. Because `productId == 0` is also `QUOTE_PRODUCT_ID` — a valid, engine-backed product — any `UpdatePrice` transaction targeting product 0 will update the engine's internal price but silently skip updating `Endpoint.priceX18[0]`, permanently desynchronizing the two price stores.

---

### Finding Description

`Clearinghouse.updatePrice` returns `(0, 0)` when `productToEngine[txn.productId]` is `address(0)`: [1](#0-0) 

`EndpointTx.sol` uses `productId != 0` as the guard before writing back to `Endpoint.priceX18`: [2](#0-1) 

When `txn.productId == 0` and `productToEngine[0] != address(0)` (i.e., the quote product has a registered engine, which is the case for the spot engine):

1. `engine.updatePrice(0, txn.priceX18)` executes — the engine's internal `RiskStore.priceX18` for product 0 is updated.
2. The function returns `(0, txn.priceX18)` — `productId` in the return is `0`.
3. `if (productId != 0)` evaluates to `false` — `Endpoint.priceX18[0]` is **never written**.

The caller in `EndpointTx.sol` cannot distinguish between "no engine found, skip" and "product 0 was successfully updated" because both cases produce a return `productId` of `0`. This is the same sentinel-value overloading class as the GMX M-21 bug: a value that legitimately equals the sentinel is silently treated as the sentinel.

`Endpoint.getPriceX18` enforces `_priceX18 != 0` as its validity check: [3](#0-2) 

If `Endpoint.priceX18[0]` was never set (default `0`), every call to `getPriceX18(0)` reverts with `ERR_INVALID_PRODUCT`. If it was set once via `setInitialPrice`, it is permanently frozen at that initial value regardless of subsequent `UpdatePrice` transactions.

`setInitialPrice` itself uses `priceX18[productId] == 0` as a one-time initialization guard: [4](#0-3) 

This means zero is overloaded with three meanings: "uninitialized," "no engine found," and "product 0 updated" — all indistinguishable at the `EndpointTx.sol` call site.

---

### Impact Explanation

`Endpoint.priceX18[0]` is the price source for `getPriceX18(0)`, which feeds `_getPriceX18` in `Clearinghouse.sol`: [5](#0-4) 

This is consumed in `settlePnl` (line 304), `checkMinDeposit`, and health calculations. If the quote product's price is ever non-trivially updated (e.g., a non-USDC quote, or a rebasing collateral), `Endpoint.priceX18[0]` will be stale while the engine's internal price is current. Any downstream consumer of `getPriceX18(0)` will use the wrong price, corrupting PnL settlement and health accounting for the quote product. [6](#0-5) 

---

### Likelihood Explanation

The trigger requires `productToEngine[0] != address(0)` — i.e., the quote product must have a registered engine. In Nado, the spot engine handles the quote product, making this condition true in normal deployment. The sequencer submitting an `UpdatePrice` for `productId == 0` is a routine operation. No privileged attacker is required; the bug fires on any valid `UpdatePrice` transaction for the quote product.

---

### Recommendation

Replace the `productId == 0` sentinel with an explicit boolean flag in the return value of `Clearinghouse.updatePrice`, or use `type(uint32).max` as the sentinel for "no engine found." This eliminates the ambiguity between "no engine registered" and "product 0 was updated":

```solidity
// Clearinghouse.sol
function updatePrice(bytes calldata transaction)
    external
    onlyEndpoint
    returns (bool updated, uint32 productId, int128 newPriceX18)
{
    ...
    if (address(engine) != address(0)) {
        engine.updatePrice(txn.productId, txn.priceX18);
        return (true, txn.productId, txn.priceX18);
    }
    return (false, 0, 0);
}

// EndpointTx.sol
(bool updated, uint32 productId, int128 newPriceX18) = clearinghouse.updatePrice(transaction);
if (updated) {
    priceX18[productId] = newPriceX18;
}
```

---

### Proof of Concept

1. Quote product (`productId == 0`) has `productToEngine[0] == address(spotEngine)` (standard deployment).
2. Sequencer submits `UpdatePrice` transaction with `txn.productId = 0`, `txn.priceX18 = 2e18`.
3. `Clearinghouse.updatePrice` passes `require(txn.priceX18 > 0)`, calls `spotEngine.updatePrice(0, 2e18)` — engine's internal `RiskStore.priceX18` for product 0 is now `2e18`.
4. Returns `(0, 2e18)` — `productId` is `0`.
5. `EndpointTx.sol`: `if (0 != 0)` → false → `Endpoint.priceX18[0]` remains at its initial value (e.g., `1e18`).
6. Any call to `Clearinghouse._getPriceX18(0)` → `Endpoint.getPriceX18(0)` → returns `1e18` (stale), not `2e18`.
7. `settlePnl` for product 0 enforces `require(txn.priceX18 == _getPriceX18(0))` — this will reject any settlement attempt using the correct current price `2e18`, or accept settlements at the stale price `1e18`, corrupting PnL accounting. [7](#0-6) [8](#0-7)

### Citations

**File:** core/contracts/Clearinghouse.sol (L301-304)
```text
            productToEngine[txn.productId] == _perpEngine(),
            ERR_INVALID_PRODUCT
        );
        require(txn.priceX18 == _getPriceX18(txn.productId), ERR_INVALID_PRICE);
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

**File:** core/contracts/Clearinghouse.sol (L694-696)
```text
    function _getPriceX18(uint32 productId) internal returns (int128) {
        return IEndpoint(getEndpoint()).getPriceX18(productId);
    }
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

**File:** core/contracts/Endpoint.sol (L322-323)
```text
        require(priceX18[productId] == 0, ERR_UNAUTHORIZED);
        priceX18[productId] = initialPriceX18;
```

**File:** core/contracts/Endpoint.sol (L334-341)
```text
    function getPriceX18(uint32 productId)
        public
        override
        returns (int128 _priceX18)
    {
        _priceX18 = priceX18[productId];
        require(_priceX18 != 0, ERR_INVALID_PRODUCT);
        emit PriceQuery(productId);
```
