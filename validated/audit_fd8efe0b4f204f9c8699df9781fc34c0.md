### Title
Sequencer Can Arbitrarily Assign Taker/Maker Roles to Extract MEV via Fill Price and Fee Manipulation - (`core/contracts/OffchainExchange.sol`)

---

### Summary

The `matchOrders` function in `OffchainExchange.sol` always executes at the **maker's price** and applies differential fees (taker pays more, maker pays less/receives rebate). The sequencer has unconstrained discretion to designate which of two crossing orders is the `taker` and which is the `maker`, absent explicit `makerOnly`/`takerOnly` flags set by the user. This allows the sequencer to choose the fill price from the spread and systematically favor one party — or their own orders — at the expense of the other.

---

### Finding Description

In `matchOrders`, the sequencer constructs the `MatchOrdersWithSigner` calldata and explicitly places one order in the `taker` slot and the other in the `maker` slot: [1](#0-0) 

The fill price is unconditionally set to the maker's `priceX18`: [2](#0-1) 

The only on-chain constraint that restricts role assignment is the `_isMakerOnly` / `_isTakerOnly` check inside `_validateOrder`, which only fires if the user explicitly encoded those flags in the order's `appendix`: [3](#0-2) 

For any standard order (neither flag set), the sequencer can freely swap which order occupies the `taker` vs. `maker` slot. Because the fill price equals `maker.order.priceX18`, the sequencer controls which price in the spread is used for settlement.

Consider two crossing orders:
- **Order A (buy):** `priceX18 = 100`, `amount > 0`
- **Order B (sell):** `priceX18 = 90`, `amount < 0`

The crossing check only requires `maker.priceX18 <= taker.priceX18` when the maker is selling: [4](#0-3) 

- If sequencer assigns **Order B (sell) as maker**: fill price = **90**. Buyer pays 90 + taker fee; seller receives 90 − maker fee.
- If sequencer assigns **Order A (buy) as maker**: fill price = **100**. Seller receives 100 − maker fee; buyer pays 100 + taker fee.

The sequencer can always choose the assignment that maximizes value extraction — e.g., always assigning their own orders as maker to receive lower fees and execute at the more favorable price.

The fee differential is applied in `applyFee`, where `taker = true` triggers the higher metered-quote fee path, while `taker = false` applies the lower maker rebate path: [5](#0-4) 

---

### Impact Explanation

The sequencer can:
1. **Extract MEV** by always assigning their own orders as maker, receiving lower fees and executing at the price most favorable to themselves.
2. **Selectively disadvantage traders** by assigning a counterparty's order as taker, forcing them to pay higher fees and execute at the less favorable end of the spread.
3. **Monetize order flow** by selling favorable maker assignment to preferred parties (e.g., market makers), creating a two-tiered market.

The corrupted state is the `quoteDelta` credited/debited to each subaccount — the difference between the two prices in the spread, multiplied by fill size, is silently redirected by the sequencer's role assignment choice.

---

### Likelihood Explanation

The sequencer is a privileged but not owner-level role — it is the normal operational path for all order matching. Every matched trade pair where neither order carries explicit `makerOnly`/`takerOnly` flags is subject to this manipulation. Standard market orders and default limit orders do not set these flags, making the vast majority of trades vulnerable. The sequencer has a direct financial incentive to exploit this on every block.

---

### Recommendation

Enforce taker/maker role assignment on-chain based on an objective, manipulation-resistant criterion. Options include:

1. **Require explicit flags**: Reject any `matchOrders` call where neither order has a `makerOnly` or `takerOnly` flag set, forcing users to declare their intended role at order-signing time.
2. **Nonce-based ordering**: Treat the order with the lower `nonce` as the maker (earlier-submitted order), mirroring the Hubble Exchange block-placement logic but using the signed nonce field.
3. **Mid-price execution**: When roles are ambiguous, execute at the midpoint of `taker.priceX18` and `maker.priceX18` to eliminate the sequencer's incentive to swap roles.

---

### Proof of Concept

1. Trader A signs a buy order: `priceX18 = 100e18`, `amount = +1e18`, no `makerOnly`/`takerOnly` flag.
2. Trader B signs a sell order: `priceX18 = 90e18`, `amount = -1e18`, no `makerOnly`/`takerOnly` flag.
3. The sequencer submits `matchOrders` with Trader A as `maker` and Trader B as `taker`.
4. Fill price = `maker.priceX18 = 100e18`. Trader B (seller) receives 100 − maker_fee per unit. Trader A (buyer) pays 100 + taker_fee per unit.
5. The sequencer resubmits with roles swapped: Trader B as `maker`, Trader A as `taker`. Fill price = 90. Trader A pays 90 + taker_fee; Trader B receives 90 − maker_fee.
6. The sequencer selects whichever assignment benefits their own position or a preferred counterparty, with no on-chain check preventing either submission.

### Citations

**File:** core/contracts/OffchainExchange.sol (L425-433)
```text
        if (isTaker) {
            if (_isMakerOnly(order.appendix)) {
                return false;
            }
        } else {
            if (_isTakerOnly(order.appendix)) {
                return false;
            }
        }
```

**File:** core/contracts/OffchainExchange.sol (L524-548)
```text
        if (taker) {
            // flat minimum fee
            if (alreadyMatched == 0) {
                meteredQuote += market.minSize;
                if (matchQuote < 0) {
                    meteredQuote = -meteredQuote;
                }
            }

            // exclude the portion on [0, self.min_size) for match_quote and
            // add to metered_quote
            // fee is only applied on [minSize, quote_amount)
            int128 feeApplied = MathHelper.abs(alreadyMatched + matchQuote) -
                market.minSize;
            feeApplied = MathHelper.min(feeApplied, matchQuote.abs());
            if (feeApplied > 0) {
                if (matchQuote < 0) {
                    feeApplied = -feeApplied;
                }
                meteredQuote += feeApplied;
            }
        } else {
            // for maker rebates things stay the same
            meteredQuote += matchQuote;
        }
```

**File:** core/contracts/OffchainExchange.sol (L640-641)
```text
        IEndpoint.SignedOrder memory taker = txn.matchOrders.taker;
        IEndpoint.SignedOrder memory maker = txn.matchOrders.maker;
```

**File:** core/contracts/OffchainExchange.sol (L737-741)
```text
        } else {
            require(
                maker.order.priceX18 <= taker.order.priceX18,
                ERR_ORDERS_CANNOT_BE_MATCHED
            );
```

**File:** core/contracts/OffchainExchange.sol (L744-762)
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

        ordersInfo.taker.amountDelta -=
            ordersInfo.taker.amountDelta %
            market.sizeIncrement;
        ordersInfo.maker.quoteDelta = ordersInfo.taker.amountDelta.mul(
            maker.order.priceX18
        );
```
