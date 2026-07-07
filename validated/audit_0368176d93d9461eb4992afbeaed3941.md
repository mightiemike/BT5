### Title
Taker Limit Price Check Performed Before Fee Deduction Allows Takers to Receive Worse Net Price Than Specified — (`core/contracts/OffchainExchange.sol`)

---

### Summary

In `OffchainExchange.matchOrders`, the limit price (slippage) check is performed against the raw execution price before taker fees and builder fees are deducted from `quoteDelta`. As a result, a taker's net received quote (for a sell) or net paid quote (for a buy) can be strictly worse than what their `priceX18` limit implies, violating the slippage guarantee the order is meant to enforce.

---

### Finding Description

In `matchOrders`, the crossing check that enforces the taker's limit price is: [1](#0-0) 

```solidity
if (maker.order.amount > 0) {
    require(
        maker.order.priceX18 >= taker.order.priceX18,
        ERR_ORDERS_CANNOT_BE_MATCHED
    );
} else {
    require(
        maker.order.priceX18 <= taker.order.priceX18,
        ERR_ORDERS_CANNOT_BE_MATCHED
    );
}
```

Execution is at the maker's price: [2](#0-1) 

```solidity
ordersInfo.maker.quoteDelta = ordersInfo.taker.amountDelta.mul(maker.order.priceX18);
ordersInfo.taker.quoteDelta = -ordersInfo.maker.quoteDelta;
```

Only **after** the price check and quote assignment does `applyFee` run: [3](#0-2) 

`applyFee` then reduces `quoteDelta` by both the protocol taker fee and the builder fee: [4](#0-3) 

```solidity
orderInfo.fee = meteredQuote - newMeteredQuote;
orderInfo.builderFee = matchQuote.abs().mul(feeInfo.builderFeeRate);
orderInfo.quoteDelta =
    orderInfo.quoteDelta -
    orderInfo.fee -
    orderInfo.builderFee;
```

The taker's final `quoteDelta` — the actual quote credited or debited to their subaccount — is therefore strictly worse than `taker.order.priceX18 × |amountDelta|`.

---

### Impact Explanation

**For a taker selling base** (`taker.order.amount < 0`):
- The check guarantees `maker.order.priceX18 >= taker.order.priceX18` (execution at or above the taker's floor).
- After fee deduction, the taker's net received quote is `maker.priceX18 × |amount| − fee − builderFee`, which is **less than** `taker.order.priceX18 × |amount|`.
- The taker receives fewer quote tokens than their stated minimum price implies.

**For a taker buying base** (`taker.order.amount > 0`):
- The check guarantees `maker.order.priceX18 <= taker.order.priceX18`.
- After fee deduction, the taker's net paid quote is `maker.priceX18 × amount + fee + builderFee`, which **exceeds** `taker.order.priceX18 × amount`.
- The taker pays more quote tokens than their stated maximum price implies.

In both cases the slippage protection encoded in `priceX18` is rendered ineffective: the taker's actual settlement is strictly worse than the limit they signed.

---

### Likelihood Explanation

This is triggered on every taker fill where fees are non-zero. The taker fee rate is set per tier via `getTierFeeRateX18`, and builder fees are additive on top. Both are non-zero for ordinary users. The sequencer submits `MatchOrders` / `MatchOrdersWithAmount` transactions, which are the only path through `matchOrders`; any taker order that crosses at exactly its limit price will settle at a net price that violates that limit. This is a deterministic, always-reachable condition for any non-zero fee tier. [5](#0-4) 

---

### Recommendation

The limit price check should be performed against the **net** quote delta after fees, not the gross execution price. Concretely, compute the fee-adjusted `quoteDelta` first (or compute the effective net price as `grossQuote − estimatedFee`) and then assert it satisfies the taker's `priceX18` bound. Alternatively, document and enforce that `priceX18` is an execution-price limit only and expose a separate `minNetQuote` / `maxNetQuote` field in the `Order` struct that is checked post-fee.

---

### Proof of Concept

1. Taker signs a sell order: `amount = -1e18` (1 unit), `priceX18 = 100e18` (minimum 100 USDC/unit).
2. Maker has a buy order at `priceX18 = 100e18`.
3. Crossing check passes: `100e18 >= 100e18` ✓.
4. `ordersInfo.taker.quoteDelta = 100e18` (100 USDC gross).
5. `applyFee` computes taker fee at, e.g., 5 bps: `fee = 0.05e18`.
6. `ordersInfo.taker.quoteDelta = 100e18 − 0.05e18 = 99.95e18`.
7. Taker's subaccount is credited **99.95 USDC**, which is below the 100 USDC minimum they specified in `priceX18`. [6](#0-5) [7](#0-6)

### Citations

**File:** core/contracts/OffchainExchange.sol (L549-565)
```text
        FeeInfo memory feeInfo = getUserFeeRateWithBuilder(
            orderInfo.sender,
            productId,
            appendix,
            taker
        );

        int128 keepRateX18 = ONE - feeInfo.feeRate;
        int128 newMeteredQuote = (meteredQuote > 0)
            ? meteredQuote.mul(keepRateX18)
            : meteredQuote.div(keepRateX18);
        orderInfo.fee = meteredQuote - newMeteredQuote;
        orderInfo.builderFee = matchQuote.abs().mul(feeInfo.builderFeeRate);
        orderInfo.quoteDelta =
            orderInfo.quoteDelta -
            orderInfo.fee -
            orderInfo.builderFee;
```

**File:** core/contracts/OffchainExchange.sol (L728-741)
```text
        require(
            (maker.order.amount > 0) != (taker.order.amount > 0),
            ERR_ORDERS_CANNOT_BE_MATCHED
        );
        if (maker.order.amount > 0) {
            require(
                maker.order.priceX18 >= taker.order.priceX18,
                ERR_ORDERS_CANNOT_BE_MATCHED
            );
        } else {
            require(
                maker.order.priceX18 <= taker.order.priceX18,
                ERR_ORDERS_CANNOT_BE_MATCHED
            );
```

**File:** core/contracts/OffchainExchange.sol (L760-777)
```text
        ordersInfo.maker.quoteDelta = ordersInfo.taker.amountDelta.mul(
            maker.order.priceX18
        );
        ordersInfo.taker.quoteDelta = -ordersInfo.maker.quoteDelta;
        ordersInfo.maker.amountDelta = -ordersInfo.taker.amountDelta;

        taker.order.amount -= ordersInfo.taker.amountDelta;
        maker.order.amount -= ordersInfo.maker.amountDelta;

        // apply the taker fee
        applyFee(
            callState.productId,
            ordersInfo.taker,
            market,
            -maker.order.priceX18.mul(filledAmounts[ordersInfo.taker.digest]),
            taker.order.appendix,
            true
        );
```
