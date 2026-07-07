### Title
Missing `taker != maker` Subaccount Check in `matchOrders()` Enables Fee Griefing — (`File: core/contracts/OffchainExchange.sol`)

---

### Summary

`OffchainExchange.matchOrders()` never verifies that `taker.order.sender != maker.order.sender`. A sequencer that submits a `MatchOrders` or `MatchOrdersWithAmount` transaction pairing a subaccount's own buy order against its own sell order will cause the contract to charge the subaccount a taker fee and consume both order digests, while the net position change is zero.

---

### Finding Description

`OffchainExchange.matchOrders()` performs the following checks before executing a trade:

- Each order is individually validated via `_validateOrder()` (signature, expiry, version, reduce-only, maker/taker-only flags).
- Orders must be crossing: `(maker.order.amount > 0) != (taker.order.amount > 0)`.
- Price must be compatible.

None of these checks prevent `taker.order.sender` and `maker.order.sender` from being the same `bytes32` subaccount. The function then:

1. Calls `applyFee(..., true)` on the taker side, deducting a taker fee (default 2 bps) from `ordersInfo.taker.quoteDelta` and crediting `market.collectedFees`.
2. Calls `applyFee(..., false)` on the maker side (maker rebate is 0 by default, so no credit).
3. Calls `_updateBalances` twice for the same subaccount with `+amountDelta` / `+takerQuoteDelta` and `-amountDelta` / `+makerQuoteDelta`. The base deltas cancel; the quote deltas sum to `-(takerFee)` — a net loss equal to the taker fee.
4. Increments `filledAmounts[ordersInfo.taker.digest]` and `filledAmounts[ordersInfo.maker.digest]`, permanently consuming both orders.

The entry path is the sequencer submitting a `TransactionType.MatchOrders` or `TransactionType.MatchOrdersWithAmount` batch. `EndpointTx.sol` decodes the transaction and calls `IOffchainExchange(offchainExchange).matchOrders(txnWithSigner)` with no check that the two senders differ. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

---

### Impact Explanation

For a self-matched pair of orders with notional value `Q`:

- Taker fee charged: `Q × 0.0002` (2 bps default) deducted from the subaccount's quote balance and added to `market.collectedFees`.
- Maker rebate: 0 (default maker rate is 0).
- Net position change: 0 (base and quote deltas cancel).
- Both order digests are marked as filled in `filledAmounts`, preventing legitimate future fills of those orders.

The subaccount suffers a direct, irreversible quote-balance loss equal to the taker fee, and loses the ability to fill the consumed orders legitimately. The fee flows into `market.collectedFees` and is eventually credited to `X_ACCOUNT` via `dumpFees()`, constituting a transfer of value away from the victim. [5](#0-4) [6](#0-5) 

---

### Likelihood Explanation

The attack requires the sequencer to include a `MatchOrders` transaction where both `taker.order.sender` and `maker.order.sender` resolve to the same subaccount. This is the direct analog of the original finding's off-chain matching engine sending wrong data. A user who holds both a live buy order and a live sell order for the same product (a common pattern for market makers or traders who flip positions) is a realistic target. The contract provides no on-chain defense-in-depth against this scenario. [4](#0-3) [7](#0-6) 

---

### Recommendation

Add a check at the top of `matchOrders()` in `OffchainExchange.sol`, after the `digestToSubaccount` remapping resolves the final senders:

```solidity
require(
    taker.order.sender != maker.order.sender,
    ERR_ORDERS_CANNOT_BE_MATCHED
);
```

This check should be placed after lines 673–678 (the `digestToSubaccount` remapping block) so that it operates on the resolved, final sender values rather than the raw order fields. [8](#0-7) 

---

### Proof of Concept

1. `user1` holds subaccount `user1:default` and has two open, valid, signed orders on product `P`:
   - Order A (taker): buy 1 unit at price `100e18` (amount = `+1e18`)
   - Order B (maker): sell 1 unit at price `90e18` (amount = `-1e18`)
   - Orders cross: maker sell price `90` ≤ taker buy price `100`.

2. Sequencer submits `TransactionType.MatchOrders` with:
   - `taker = SignedOrder{ order: OrderA, signature: sigA }`
   - `maker = SignedOrder{ order: OrderB, signature: sigB }`
   - Both `taker.order.sender` and `maker.order.sender` = `user1:default`.

3. `EndpointTx` decodes and calls `offchainExchange.matchOrders(txnWithSigner)`.

4. Inside `matchOrders`:
   - Both orders pass `_validateOrder` (valid signatures, not expired, correct version).
   - Crossing check passes (`+1e18` vs `-1e18`).
   - Price check passes (`90 ≤ 100`).
   - Execution at maker price `90e18`: `amountDelta = 1e18`, `quoteDelta = 90e18`.
   - Taker fee = `90e18 × 0.0002 = 0.018e18` deducted from `user1:default`'s quote balance.
   - `_updateBalances` called twice for `user1:default`: `+1e18` base / `-90.018e18` quote (taker), then `-1e18` base / `+90e18` quote (maker). Net: `0` base, `-0.018e18` quote.
   - `filledAmounts[digestA]` and `filledAmounts[digestB]` both incremented.

5. Result: `user1:default` loses `0.018e18` quote tokens in fees, both orders are consumed, and no actual trade occurred. [9](#0-8) [3](#0-2)

### Citations

**File:** core/contracts/OffchainExchange.sol (L509-570)
```text
    function applyFee(
        uint32 productId,
        OrderInfo memory orderInfo,
        MarketInfo memory market,
        int128 alreadyMatched, // in quote
        uint128 appendix,
        bool taker
    ) internal {
        // X account is passthrough for trading and incurs
        // no fees
        if (orderInfo.sender == X_ACCOUNT) {
            return;
        }
        int128 matchQuote = orderInfo.quoteDelta;
        int128 meteredQuote = 0;
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
        if (orderInfo.builderFee > 0) {
            collectedBuilderFee[market.quoteId][feeInfo.builderId] += orderInfo
                .builderFee;
            emitBuilderEvent(orderInfo, feeInfo.builderId, productId);
        }
```

**File:** core/contracts/OffchainExchange.sol (L631-651)
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
            !RiskHelper.isIsolatedSubaccount(taker.order.sender),
            ERR_INVALID_TAKER
        );
        require(
            !RiskHelper.isIsolatedSubaccount(maker.order.sender),
            ERR_INVALID_MAKER
        );
```

**File:** core/contracts/OffchainExchange.sol (L673-678)
```text
        if (digestToSubaccount[ordersInfo.taker.digest] != bytes32(0)) {
            taker.order.sender = digestToSubaccount[ordersInfo.taker.digest];
        }
        if (digestToSubaccount[ordersInfo.maker.digest] != bytes32(0)) {
            maker.order.sender = digestToSubaccount[ordersInfo.maker.digest];
        }
```

**File:** core/contracts/OffchainExchange.sol (L727-764)
```text
        // ensure orders are crossing
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
        }

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
        ordersInfo.taker.quoteDelta = -ordersInfo.maker.quoteDelta;
        ordersInfo.maker.amountDelta = -ordersInfo.taker.amountDelta;
```

**File:** core/contracts/OffchainExchange.sol (L769-794)
```text
        // apply the taker fee
        applyFee(
            callState.productId,
            ordersInfo.taker,
            market,
            -maker.order.priceX18.mul(filledAmounts[ordersInfo.taker.digest]),
            taker.order.appendix,
            true
        );

        // apply the maker fee
        if (makerAccruesTakerFee(maker.order.sender, callState.productId)) {
            ordersInfo.maker.fee = -ordersInfo.taker.fee;
            ordersInfo.maker.quoteDelta =
                ordersInfo.maker.quoteDelta +
                ordersInfo.taker.fee;
        } else {
            applyFee(
                callState.productId,
                ordersInfo.maker,
                market,
                0, // alreadyMatched doesn't matter for a maker order
                maker.order.appendix,
                false
            );
        }
```

**File:** core/contracts/OffchainExchange.sol (L811-840)
```text
        _updateBalances(
            callState,
            market.quoteId,
            taker.order.sender,
            ordersInfo.taker.amountDelta,
            ordersInfo.taker.quoteDelta
        );
        _updateBalances(
            callState,
            market.quoteId,
            maker.order.sender,
            ordersInfo.maker.amountDelta,
            ordersInfo.maker.quoteDelta
        );

        require(isHealthy(taker.order.sender), ERR_INVALID_TAKER);
        require(isHealthy(maker.order.sender), ERR_INVALID_MAKER);

        marketInfo[callState.productId].collectedFees = market.collectedFees;

        if (taker.order.sender != X_ACCOUNT) {
            filledAmounts[ordersInfo.taker.digest] += ordersInfo
                .taker
                .amountDelta;
        }
        if (maker.order.sender != X_ACCOUNT) {
            filledAmounts[ordersInfo.maker.digest] += ordersInfo
                .maker
                .amountDelta;
        }
```

**File:** core/contracts/OffchainExchange.sol (L891-930)
```text
    function dumpFees() external onlyEndpoint {
        // loop over all spot and perp product ids
        uint32[] memory productIds = spotEngine.getProductIds();

        for (uint32 i = 1; i < productIds.length; i++) {
            uint32 productId = productIds[i];
            MarketInfoStore memory market = marketInfo[productId];
            if (market.collectedFees == 0) {
                continue;
            }

            spotEngine.updateBalance(
                quoteIds[productId],
                X_ACCOUNT,
                market.collectedFees
            );

            market.collectedFees = 0;
            marketInfo[productId] = market;
        }

        productIds = perpEngine.getProductIds();

        for (uint32 i = 0; i < productIds.length; i++) {
            uint32 productId = productIds[i];
            MarketInfoStore memory market = marketInfo[productId];
            if (market.collectedFees == 0) {
                continue;
            }

            perpEngine.updateBalance(
                productId,
                X_ACCOUNT,
                0,
                market.collectedFees
            );

            market.collectedFees = 0;
            marketInfo[productId] = market;
        }
```

**File:** core/contracts/EndpointTx.sol (L495-514)
```text
        } else if (txType == IEndpoint.TransactionType.MatchOrders) {
            IEndpoint.MatchOrders memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.MatchOrders)
            );
            requireSubaccount(txn.taker.order.sender);
            requireSubaccount(txn.maker.order.sender);

            IEndpoint.MatchOrdersWithSigner memory txnWithSigner = IEndpoint
                .MatchOrdersWithSigner({
                    matchOrders: txn,
                    takerLinkedSigner: getLinkedSignerOrNlpSigner(
                        txn.taker.order.sender
                    ),
                    makerLinkedSigner: getLinkedSignerOrNlpSigner(
                        txn.maker.order.sender
                    ),
                    takerAmountDelta: 0
                });
            IOffchainExchange(offchainExchange).matchOrders(txnWithSigner);
```

**File:** core/contracts/EndpointTx.sol (L515-533)
```text
        } else if (txType == IEndpoint.TransactionType.MatchOrdersWithAmount) {
            IEndpoint.MatchOrdersWithAmount memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.MatchOrdersWithAmount)
            );
            requireSubaccount(txn.matchOrders.taker.order.sender);
            requireSubaccount(txn.matchOrders.maker.order.sender);
            IEndpoint.MatchOrdersWithSigner memory txnWithSigner = IEndpoint
                .MatchOrdersWithSigner({
                    matchOrders: txn.matchOrders,
                    takerLinkedSigner: getLinkedSignerOrNlpSigner(
                        txn.matchOrders.taker.order.sender
                    ),
                    makerLinkedSigner: getLinkedSignerOrNlpSigner(
                        txn.matchOrders.maker.order.sender
                    ),
                    takerAmountDelta: txn.takerAmountDelta
                });
            IOffchainExchange(offchainExchange).matchOrders(txnWithSigner);
```
