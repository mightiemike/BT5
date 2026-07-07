### Title
Reduce-Only Order Cap Computed But Discarded — Execution Uses Original Uncapped Amount - (File: `core/contracts/OffchainExchange.sol`)

### Summary
`_validateOrder` computes a position-capped `order.amount` for reduce-only orders but stores it in a local memory copy that is never propagated back. `matchOrders` then executes using the original uncapped `taker.order.amount`, nullifying the reduce-only constraint entirely.

### Finding Description

In `_validateOrder`, a local copy of the order is created and its amount is capped to the current position size for reduce-only orders: [1](#0-0) 

```solidity
IEndpoint.Order memory order = signedOrder.order;   // local copy
int128 filledAmount = filledAmounts[orderDigest];
order.amount -= filledAmount;

if (_isReduceOnly(order.appendix)) {
    int128 amount = callState.isPerp
        ? callState.perp.getBalance(...).amount
        : callState.spot.getBalance(...).amount;
    if ((order.amount > 0) == (amount > 0)) {
        order.amount = 0;
    } else if (order.amount > 0) {
        order.amount = MathHelper.min(order.amount, -amount);  // capped
    } else if (order.amount < 0) {
        order.amount = MathHelper.max(order.amount, -amount);  // capped
    }
}
return ... && (order.amount != 0) && ...;   // only used for validity gate
```

Because `order` is a fresh memory copy of `signedOrder.order`, the capped value is never written back. The caller's `taker.order.amount` remains the original signed value.

Back in `matchOrders`, execution uses the original uncapped amount: [2](#0-1) 

```solidity
if (taker.order.amount < 0) {
    ordersInfo.taker.amountDelta = MathHelper.max(
        taker.order.amount,        // ← original, not the capped value
        -maker.order.amount
    );
} else if (taker.order.amount > 0) {
    ordersInfo.taker.amountDelta = MathHelper.min(
        taker.order.amount,        // ← original, not the capped value
        -maker.order.amount
    );
}
```

The capped amount is computed solely to pass the `(order.amount != 0)` gate in `_validateOrder` and is then silently discarded. This is structurally identical to the reported bug: `rateLimitedRewardInUSDV` is computed but `rewardInUSDV` is used.

### Impact Explanation

A reduce-only order is a user-signed commitment that the trade must only decrease an existing position. Because the cap is not enforced on-chain:

- A reduce-only sell order for 100 units on a +10 long position passes validation (capped to −10 ≠ 0) but executes for the full −100, flipping the position to −90.
- The user's margin profile changes drastically and unexpectedly, potentially triggering immediate liquidation.
- The on-chain invariant "reduce-only orders cannot increase net exposure" is broken. Any accounting or health check that relies on this invariant is corrupted.

Corrupted state: `spotEngine`/`perpEngine` balance for the taker subaccount diverges from what the user authorised, and `filledAmounts[digest]` records the full uncapped fill, permanently consuming the order.

### Likelihood Explanation

`matchOrders` is gated by `onlyEndpoint` and is submitted by the sequencer. Exploitation requires the sequencer to submit a match whose `amountDelta` exceeds the taker's position size. This can occur:

1. **Honest sequencer, stale state**: the sequencer reads position size off-chain, a concurrent settlement or liquidation reduces it before the match lands on-chain, and the on-chain contract provides no safety net.
2. **Malicious or compromised sequencer**: deliberately matches a reduce-only order for the full signed amount.

The protocol's own wiki describes slow-mode as a censorship-resistance path where users can submit transactions directly to the Endpoint; if `MatchOrdersWithSigner` is reachable via slow-mode, an unprivileged user can self-match (taker = maker = same address, both orders signed by the user) to trigger the bug without any sequencer involvement.

### Recommendation

Propagate the capped amount back to `signedOrder.order.amount` instead of a local copy, or return the effective amount from `_validateOrder` and use it in `matchOrders` when computing `amountDelta`:

```solidity
// Option A: write back to signedOrder
signedOrder.order.amount = order.amount;   // after reduce-only capping

// Option B: use the capped amount in matchOrders
int128 effectiveTakerAmount = _getEffectiveAmount(_validateOrder(...));
ordersInfo.taker.amountDelta = MathHelper.max(effectiveTakerAmount, -maker.order.amount);
```

### Proof of Concept

1. Trader holds a perp long of +10 units.
2. Trader signs a reduce-only sell order: `amount = -100`, `appendix` has reduce-only bit set.
3. A counterparty (or the trader themselves via slow-mode) signs a buy order: `amount = +100`.
4. `matchOrders` is submitted with these two orders.
5. `_validateOrder` for the taker: local `order.amount` is capped to `max(-100, -10) = -10`; `(order.amount != 0)` → true; returns `true`. `taker.order.amount` in the caller is still `−100`.
6. `ordersInfo.taker.amountDelta = max(-100, -100) = -100`.
7. `_updateBalances` applies `−100` to the taker's perp balance: position goes from `+10` to `−90`.
8. `filledAmounts[digest] += -100` — the full order is consumed.
9. Taker now holds a large short position they never authorised, with margin requirements that may immediately trigger liquidation. [3](#0-2) [4](#0-3)

### Citations

**File:** core/contracts/OffchainExchange.sol (L410-469)
```text
    function _validateOrder(
        CallState memory callState,
        MarketInfo memory,
        IEndpoint.SignedOrder memory signedOrder,
        bytes32 orderDigest,
        bool isTaker,
        address linkedSigner
    ) internal view returns (bool) {
        if ((signedOrder.order.appendix & 255) != orderVersion()) {
            return false;
        }
        if (signedOrder.order.sender == X_ACCOUNT) {
            return true;
        }
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

        if (_isReduceOnly(order.appendix)) {
            int128 amount = callState.isPerp
                ? callState
                    .perp
                    .getBalance(callState.productId, order.sender)
                    .amount
                : callState
                    .spot
                    .getBalance(callState.productId, order.sender)
                    .amount;
            if ((order.amount > 0) == (amount > 0)) {
                order.amount = 0;
            } else if (order.amount > 0) {
                order.amount = MathHelper.min(order.amount, -amount);
            } else if (order.amount < 0) {
                order.amount = MathHelper.max(order.amount, -amount);
            }
        }

        return
            ((order.priceX18 > 0) || _isTWAP(order.appendix)) &&
            (signedOrder.order.sender == N_ACCOUNT ||
                _checkSignature(
                    order.sender,
                    orderDigest,
                    linkedSigner,
                    signedOrder.signature
                )) &&
            // valid amount
            (order.amount != 0) &&
            !_expired(order.expiration);
    }
```

**File:** core/contracts/OffchainExchange.sol (L631-844)
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

        ordersInfo = OrdersInfo(
            OrderInfo({
                digest: getDigest(callState.productId, taker.order),
                sender: taker.order.sender,
                amount: taker.order.amount,
                fee: 0,
                builderFee: 0,
                quoteDelta: 0,
                amountDelta: 0
            }),
            OrderInfo({
                digest: getDigest(callState.productId, maker.order),
                sender: maker.order.sender,
                amount: maker.order.amount,
                fee: 0,
                builderFee: 0,
                quoteDelta: 0,
                amountDelta: 0
            })
        );
        if (digestToSubaccount[ordersInfo.taker.digest] != bytes32(0)) {
            taker.order.sender = digestToSubaccount[ordersInfo.taker.digest];
        }
        if (digestToSubaccount[ordersInfo.maker.digest] != bytes32(0)) {
            maker.order.sender = digestToSubaccount[ordersInfo.maker.digest];
        }

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

        if (txn.takerAmountDelta != 0) {
            require(_isTWAP(taker.order.appendix), ERR_INVALID_TAKER);
            require(
                (txn.takerAmountDelta > 0) == (taker.order.amount > 0),
                ERR_INVALID_TAKER
            );
            if (taker.order.amount > 0) {
                require(
                    taker.order.amount >= txn.takerAmountDelta &&
                        maker.order.amount <= -txn.takerAmountDelta,
                    ERR_INVALID_TAKER
                );
            } else {
                require(
                    taker.order.amount <= txn.takerAmountDelta &&
                        maker.order.amount >= -txn.takerAmountDelta,
                    ERR_INVALID_TAKER
                );
            }

            taker.order.amount = txn.takerAmountDelta;
            maker.order.amount = -txn.takerAmountDelta;
        }

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

        updateCollectedFees(
            callState.productId,
            market,
            true,
            ordersInfo.taker.fee,
            ordersInfo.taker.builderFee
        );
        updateCollectedFees(
            callState.productId,
            market,
            false,
            ordersInfo.maker.fee,
            ordersInfo.maker.builderFee
        );

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

        emitFillOrderEvent(callState, ordersInfo.maker, maker.order, false);
        emitFillOrderEvent(callState, ordersInfo.taker, taker.order, true);
    }
```
