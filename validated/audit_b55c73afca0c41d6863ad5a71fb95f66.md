### Title
Taker Order `priceX18` Limit Not Guaranteed After Fee Deduction — (File: `core/contracts/OffchainExchange.sol`)

---

### Summary

In `OffchainExchange.sol`, the price-crossing check validates a taker's limit price (`priceX18`) against the execution price **before** protocol fees are applied. After `applyFee` deducts the taker fee and builder fee from `quoteDelta`, the effective price the taker actually receives is worse than their signed limit price. A sell taker who specifies `priceX18 = P` as their minimum acceptable price will receive less than `P` per unit of base asset after fees; a buy taker who specifies `priceX18 = P` as their maximum acceptable price will pay more than `P` per unit after fees.

---

### Finding Description

In `matchOrders`, the price-crossing invariant is enforced at lines 732–741:

```solidity
if (maker.order.amount > 0) {
    require(maker.order.priceX18 >= taker.order.priceX18, ERR_ORDERS_CANNOT_BE_MATCHED);
} else {
    require(maker.order.priceX18 <= taker.order.priceX18, ERR_ORDERS_CANNOT_BE_MATCHED);
}
``` [1](#0-0) 

Execution then occurs at the maker's price:

```solidity
ordersInfo.maker.quoteDelta = ordersInfo.taker.amountDelta.mul(maker.order.priceX18);
ordersInfo.taker.quoteDelta = -ordersInfo.maker.quoteDelta;
``` [2](#0-1) 

Immediately after, `applyFee` is called for the taker, which modifies `quoteDelta`:

```solidity
orderInfo.quoteDelta = orderInfo.quoteDelta - orderInfo.fee - orderInfo.builderFee;
``` [3](#0-2) 

The fee computation for a sell taker (positive `quoteDelta`) uses:

```solidity
int128 keepRateX18 = ONE - feeInfo.feeRate;
int128 newMeteredQuote = (meteredQuote > 0)
    ? meteredQuote.mul(keepRateX18)
    : meteredQuote.div(keepRateX18);
orderInfo.fee = meteredQuote - newMeteredQuote;
``` [4](#0-3) 

For a sell taker whose order executes exactly at their limit price `P` (i.e., `maker.priceX18 == taker.priceX18 == P`):

- Pre-fee `quoteDelta` = `amount * P`
- Post-fee `quoteDelta` = `amount * P * (1 - feeRate)` < `amount * P`
- Effective price per unit = `P * (1 - feeRate)` < `P`

The taker's signed `priceX18` is violated. The same logic applies in reverse for a buy taker: the effective price paid per unit is `P / (1 - feeRate)` > `P`.

The default taker fee rate is 2 bps (`200_000_000_000_000` in X18 representation): [5](#0-4) 

---

### Impact Explanation

A taker who signs an order with `priceX18 = P` as their limit receives a post-fee effective price that violates the limit they committed to on-chain. For a sell taker at exactly the limit, the shortfall is `amount * P * feeRate`. At the default 2 bps taker rate on a $1,000,000 notional trade, the taker receives $200 less than their stated minimum. With builder fees (`builderFeeRate`) also deducted from `quoteDelta`, the gap widens further. The corrupted state is the taker's `quoteDelta` balance in the spot or perp engine, which is credited below the amount implied by the signed limit price.

---

### Likelihood Explanation

This affects every taker order that executes at or near the taker's limit price. It is not an edge case: any order that is filled at the worst acceptable price (a common scenario for limit orders near the market) will have its post-fee effective price breach the signed limit. The sequencer-driven `matchOrders` path is the normal execution path for all trades, making this reachable by any unprivileged trader submitting a signed order.

---

### Recommendation

Apply fees before validating the price-crossing invariant, or enforce the price-crossing check against the post-fee effective price. Concretely, after computing `applyFee`, verify that the taker's effective price (post-fee `quoteDelta` / `amountDelta`) still satisfies the taker's `priceX18` limit. If it does not, revert with `ERR_SLIPPAGE_TOO_HIGH` (already defined in `Errors.sol`). [6](#0-5) 

---

### Proof of Concept

1. Sell taker signs an order: `priceX18 = 100e18`, `amount = -1e18` (sell 1 unit, minimum price 100).
2. Maker signs a matching order: `priceX18 = 100e18`, `amount = 1e18` (buy 1 unit at 100).
3. Price-crossing check passes: `maker.priceX18 (100) >= taker.priceX18 (100)`.
4. `taker.quoteDelta` is set to `+100e18` (1 unit × 100 price).
5. `applyFee` is called: `keepRateX18 = 1e18 - 200_000_000_000_000 = 999_800_000_000_000_000`.
6. `newMeteredQuote = 100e18 * 0.9998 = 99.98e18`.
7. `fee = 100e18 - 99.98e18 = 0.02e18`.
8. Final `taker.quoteDelta = 100e18 - 0.02e18 = 99.98e18`.
9. Effective price received = `99.98e18 / 1e18 = 99.98` — **below the taker's signed minimum of 100**.

The taker's `priceX18` guarantee is broken by the fee deduction that occurs after the crossing check, directly mirroring the BarnBridge M04 pattern where `minGain_` was validated before protocol fees were subtracted.

### Citations

**File:** core/contracts/OffchainExchange.sol (L556-560)
```text
        int128 keepRateX18 = ONE - feeInfo.feeRate;
        int128 newMeteredQuote = (meteredQuote > 0)
            ? meteredQuote.mul(keepRateX18)
            : meteredQuote.div(keepRateX18);
        orderInfo.fee = meteredQuote - newMeteredQuote;
```

**File:** core/contracts/OffchainExchange.sol (L562-565)
```text
        orderInfo.quoteDelta =
            orderInfo.quoteDelta -
            orderInfo.fee -
            orderInfo.builderFee;
```

**File:** core/contracts/OffchainExchange.sol (L732-741)
```text
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

**File:** core/contracts/OffchainExchange.sol (L760-763)
```text
        ordersInfo.maker.quoteDelta = ordersInfo.taker.amountDelta.mul(
            maker.order.priceX18
        );
        ordersInfo.taker.quoteDelta = -ordersInfo.maker.quoteDelta;
```

**File:** core/contracts/OffchainExchange.sol (L942-945)
```text
            FeeRates({
                makerRateX18: 0,
                takerRateX18: 200_000_000_000_000 // 2 bps
            });
```

**File:** core/contracts/common/Errors.sol (L90-90)
```text
string constant ERR_SLIPPAGE_TOO_HIGH = "STH";
```
