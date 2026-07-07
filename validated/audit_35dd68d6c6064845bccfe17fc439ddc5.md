### Title
Partially-Filled Maker Order Can Be Over-Filled Beyond Signed Quantity — (`core/contracts/OffchainExchange.sol`)

### Summary
`matchOrders` computes the fill size using the maker's **original signed amount** rather than the **remaining unfilled amount**. Because `_validateOrder` adjusts `order.amount` for partial fills only inside a local memory copy that is never written back to the caller, the matching arithmetic at lines 745–754 always sees the full original quantity. A partially-filled maker order can therefore be matched for more units than the maker ever authorized, corrupting `filledAmounts` and forcing an unauthorized asset debit on the maker.

---

### Finding Description

`_validateOrder` receives `IEndpoint.SignedOrder memory signedOrder` — a **value copy** in Solidity. Inside the function it creates a second local copy and subtracts the already-filled amount:

```solidity
IEndpoint.Order memory order = signedOrder.order;   // local copy
int128 filledAmount = filledAmounts[orderDigest];
order.amount -= filledAmount;                        // only affects local copy
``` [1](#0-0) 

The function returns `true`/`false` but **never writes the adjusted amount back** to the caller's `maker` variable. After `_validateOrder` returns, `maker.order.amount` in `matchOrders` still holds the original signed quantity.

The fill-size computation then uses this stale value:

```solidity
ordersInfo.taker.amountDelta = MathHelper.min(
    taker.order.amount,
    -maker.order.amount   // ← original amount, not remaining
);
``` [2](#0-1) 

The resulting `amountDelta` is then accumulated into `filledAmounts`:

```solidity
filledAmounts[ordersInfo.maker.digest] += ordersInfo.maker.amountDelta;
``` [3](#0-2) 

Because `amountDelta` was computed against the full original amount, `filledAmounts[maker.digest]` can grow past the original signed quantity, and the maker's engine balance is debited for the inflated amount via `_updateBalances`. [4](#0-3) 

---

### Impact Explanation

A maker who signs an order for **N** units and has already been filled for **k** units (k < N) has only **N − k** units remaining. The bug allows a single subsequent `matchOrders` call to fill up to **N** additional units, producing a total fill of **k + N** — exceeding the signed quantity by **k** units. The maker's spot or perp balance is debited for the excess, constituting a direct, unauthorized asset transfer. The `filledAmounts` invariant (total fill ≤ signed quantity) is broken.

---

### Likelihood Explanation

The sequencer/builder submits `matchOrders` transactions. A malicious or compromised sequencer can deliberately pair a partially-filled resting maker order with an oversized taker order to trigger the over-fill. Even an honest sequencer with a batching bug could trigger this inadvertently. Because the on-chain code provides no guard against over-filling, the maker has no protection once their order is resting.

---

### Recommendation

Before the fill-size computation, subtract `filledAmounts[ordersInfo.maker.digest]` from `maker.order.amount` to obtain the true remaining quantity:

```solidity
maker.order.amount -= filledAmounts[ordersInfo.maker.digest];
```

Apply the same adjustment to `taker.order.amount` using `filledAmounts[ordersInfo.taker.digest]`. This mirrors the adjustment already performed inside `_validateOrder` and ensures the matching arithmetic is bounded by the actual remaining quantities.

---

### Proof of Concept

1. Maker signs an order: `amount = 100`, price = P. Digest = `D_m`.
2. First `matchOrders` call: taker fills 70 units.
   - `filledAmounts[D_m] = 70`. Maker balance debited 70.
3. Second `matchOrders` call: taker `amount = 100`.
   - `_validateOrder` computes `100 − 70 = 30 ≠ 0` → returns `true`.
   - Back in `matchOrders`, `maker.order.amount` is still **100** (unadjusted).
   - `amountDelta = min(100, 100) = 100`.
   - `filledAmounts[D_m] += 100` → **170** (exceeds signed 100 by 70).
   - Maker balance debited an additional **100** units — 70 units beyond what was authorized. [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** core/contracts/OffchainExchange.sol (L424-436)
```text
        IEndpoint.Order memory order = signedOrder.order;
        if (isTaker) {
            if (_isMakerOnly(order.appendix)) {
                return false;
            }
        } else {
            if (_isTakerOnly(order.appendix)) {
                return false;
            }
        }

        int128 filledAmount = filledAmounts[orderDigest];
        order.amount -= filledAmount;
```

**File:** core/contracts/OffchainExchange.sol (L631-644)
```text
    function matchOrders(IEndpoint.MatchOrdersWithSigner calldata txn)
        external
        onlyEndpoint
    {
        CallState memory callState = _getCallState(txn.matchOrders.productId);

        OrdersInfo memory ordersInfo;

        MarketInfo memory market = getMarketInfo(callState.productId);
        IEndpoint.SignedOrder memory taker = txn.matchOrders.taker;
        IEndpoint.SignedOrder memory maker = txn.matchOrders.maker;

        // isolated subaccounts cannot be used as sender
        require(
```

**File:** core/contracts/OffchainExchange.sol (L680-701)
```text
        require(
            _validateOrder(
                callState,
                market,
                taker,
                ordersInfo.taker.digest,
                true,
                txn.takerLinkedSigner
            ),
            ERR_INVALID_TAKER
        );
        require(
            _validateOrder(
                callState,
                market,
                maker,
                ordersInfo.maker.digest,
                false,
                txn.makerLinkedSigner
            ),
            ERR_INVALID_MAKER
        );
```

**File:** core/contracts/OffchainExchange.sol (L744-755)
```text
        // execution happens at the maker's price
        if (taker.order.amount < 0) {
            ordersInfo.taker.amountDelta = MathHelper.max(
                taker.order.amount,
                -maker.order.amount
            );
        } else if (taker.order.amount > 0) {
            ordersInfo.taker.amountDelta = MathHelper.min(
                taker.order.amount,
                -maker.order.amount
            );
        }
```

**File:** core/contracts/OffchainExchange.sol (L818-824)
```text
        _updateBalances(
            callState,
            market.quoteId,
            maker.order.sender,
            ordersInfo.maker.amountDelta,
            ordersInfo.maker.quoteDelta
        );
```

**File:** core/contracts/OffchainExchange.sol (L836-840)
```text
        if (maker.order.sender != X_ACCOUNT) {
            filledAmounts[ordersInfo.maker.digest] += ordersInfo
                .maker
                .amountDelta;
        }
```
