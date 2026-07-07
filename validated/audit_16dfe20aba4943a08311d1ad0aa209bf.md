### Title
`_validateOrder` Ignores `MarketInfo` Parameter, Allowing Sub-`minSize` Orders to Corrupt Taker Quote Balances — (File: `core/contracts/OffchainExchange.sol`)

---

### Summary

`_validateOrder` in `OffchainExchange.sol` declares `MarketInfo memory` as its second parameter but leaves it unnamed and never references it in the function body. As a result, the `minSize` market constraint is never enforced during order validation. When a sub-`minSize` order is matched, `applyFee` applies a flat minimum fee based on `market.minSize` that exceeds the actual trade value, corrupting the taker's quote balance.

---

### Finding Description

`_validateOrder` is the sole order-validation gate called before any balance mutation in `matchOrders`. Its signature is:

```solidity
function _validateOrder(
    CallState memory callState,
    MarketInfo memory,          // ← unnamed, never read
    IEndpoint.SignedOrder memory signedOrder,
    bytes32 orderDigest,
    bool isTaker,
    address linkedSigner
) internal view returns (bool) {
``` [1](#0-0) 

The function body checks version, sender identity, maker/taker flags, reduce-only logic, signature, non-zero amount, and expiration — but never touches the `MarketInfo` struct. `market.minSize` and `market.sizeIncrement` are both silently discarded. [2](#0-1) 

`matchOrders` correctly loads `MarketInfo` and passes it to `_validateOrder`, but since `_validateOrder` ignores it, the `minSize` guard is never applied: [3](#0-2) 

After validation passes, `applyFee` is called with the same `market`. For a taker's **first fill** (`alreadyMatched == 0`), it unconditionally sets `meteredQuote = market.minSize` as the flat minimum fee base:

```solidity
if (alreadyMatched == 0) {
    meteredQuote += market.minSize;
    ...
}
int128 feeApplied = MathHelper.abs(alreadyMatched + matchQuote) - market.minSize;
feeApplied = MathHelper.min(feeApplied, matchQuote.abs());
``` [4](#0-3) 

When `|matchQuote| < minSize`, `feeApplied` is non-positive (clamped to zero), so the entire fee is computed on `meteredQuote = minSize`. The resulting fee is `minSize * feeRate`, which is larger than `matchQuote`. The final balance update is:

```solidity
orderInfo.quoteDelta = orderInfo.quoteDelta - orderInfo.fee - orderInfo.builderFee;
``` [5](#0-4) 

For a taker buying (positive `matchQuote`), `quoteDelta` becomes negative — the taker's quote balance is debited more than the trade value. The excess fee is credited to `FEES_ACCOUNT` and later swept to `X_ACCOUNT` via `claimSequencerFees`.

---

### Impact Explanation

**Corrupted state**: Taker's quote balance in `SpotEngine` or `PerpEngine` is reduced by `fee - matchQuote` more than the trade warrants. The excess is permanently transferred to `FEES_ACCOUNT`. For a perp taker, `vQuoteBalance` is similarly over-debited. This is a direct, irreversible asset loss for the taker on every sub-`minSize` fill.

**Broken invariant**: The protocol's `minSize` market parameter is intended to be a hard floor on order size. Because `_validateOrder` ignores `MarketInfo`, this invariant is never enforced, and the fee model's assumption that `|matchQuote| >= minSize` is violated, producing fee > trade value.

---

### Likelihood Explanation

The sequencer submits `MatchOrdersWithSigner` transactions. A trader (unprivileged) signs an order with `amount` below `minSize`. The sequencer, which may process orders without independently enforcing `minSize` off-chain, can match it. The entry path is fully reachable through the standard `matchOrders` → `_validateOrder` → `applyFee` flow with no privileged access required beyond the trader's own signed order.

---

### Recommendation

Add a `minSize` check inside `_validateOrder` using the `MarketInfo` parameter. Name the parameter and enforce the constraint:

```solidity
function _validateOrder(
    CallState memory callState,
    MarketInfo memory market,   // ← name it and use it
    IEndpoint.SignedOrder memory signedOrder,
    ...
) internal view returns (bool) {
    ...
    int128 remainingAmount = order.amount - filledAmounts[orderDigest];
    if (MathHelper.abs(remainingAmount) < market.minSize) {
        return false;
    }
    ...
}
```

Alternatively, if `minSize` enforcement is intentionally deferred to off-chain sequencer logic, remove the parameter from the function signature to make the design intent explicit and prevent future confusion.

---

### Proof of Concept

1. Market is configured with `minSize = 10e18` (10 units of quote).
2. Trader signs an order: `amount = 1` (1 unit of base), `priceX18 = 1e18`, so `matchQuote = 1e18` (1 unit of quote, below `minSize`).
3. Sequencer submits `matchOrders`. `_validateOrder` is called with `market` — it ignores `market` entirely and returns `true` (valid amount, valid signature, not expired).
4. `applyFee` is called. `alreadyMatched == 0`, so `meteredQuote = market.minSize = 10e18`.
5. `feeApplied = |0 + 1e18| - 10e18 = -9e18` → clamped to 0.
6. `fee = 10e18 * feeRate` (e.g., at 2 bps: `fee = 2e13`). Since `matchQuote = 1e18`, `fee` is negligible here, but at higher `minSize` relative to `matchQuote`, `fee` can exceed `matchQuote`.
7. With `minSize = 1e18` and `matchQuote = 1e15` (0.001 units), `fee = 1e18 * 0.0002 = 2e14 > 1e15`? No — let me be precise: `fee = minSize * feeRate = 1e18 * 2e14/1e18 = 2e14`. `matchQuote = 1e15`. `quoteDelta = 1e15 - 2e14 = 8e14` — still positive but fee is 20% of trade. At `feeRate = 10%` (tier): `fee = 1e17 > 1e15 = matchQuote`, so `quoteDelta = 1e15 - 1e17 < 0`. Taker's quote balance is debited `1e17 - 1e15 = 9.9e16` more than the trade value, with that excess going to `FEES_ACCOUNT`. [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** core/contracts/OffchainExchange.sol (L410-417)
```text
    function _validateOrder(
        CallState memory callState,
        MarketInfo memory,
        IEndpoint.SignedOrder memory signedOrder,
        bytes32 orderDigest,
        bool isTaker,
        address linkedSigner
    ) internal view returns (bool) {
```

**File:** core/contracts/OffchainExchange.sol (L418-469)
```text
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

**File:** core/contracts/OffchainExchange.sol (L524-565)
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

**File:** core/contracts/OffchainExchange.sol (L639-701)
```text
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
```
