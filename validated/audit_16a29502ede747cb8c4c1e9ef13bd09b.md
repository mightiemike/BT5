### Title
Reduce-Only Order Amount Cap Computed But Never Applied to Execution — (`File: core/contracts/OffchainExchange.sol`)

---

### Summary

`_validateOrder()` caps a reduce-only order's `amount` to the current position size on a **local memory copy**, then validates that the capped amount is non-zero. However, `matchOrders()` computes `amountDelta` from the **original, uncapped** `taker.order.amount`. The reduce-only constraint is checked but never enforced in execution, allowing a reduce-only order to flip a position rather than merely close it.

---

### Finding Description

In `OffchainExchange.sol`, `_validateOrder()` handles reduce-only orders as follows:

```solidity
IEndpoint.Order memory order = signedOrder.order;   // local memory copy
if (_isReduceOnly(order.appendix)) {
    int128 amount = callState.isPerp
        ? callState.perp.getBalance(...).amount
        : callState.spot.getBalance(...).amount;
    if ((order.amount > 0) == (amount > 0)) {
        order.amount = 0;
    } else if (order.amount > 0) {
        order.amount = MathHelper.min(order.amount, -amount);
    } else if (order.amount < 0) {
        order.amount = MathHelper.max(order.amount, -amount);
    }
}
return ... && (order.amount != 0) && ...;
``` [1](#0-0) 

The capping is applied to `order`, a **local copy** of `signedOrder.order`. The original `taker.order.amount` in `matchOrders()` is never modified.

Back in `matchOrders()`, `amountDelta` is computed directly from the original signed amounts:

```solidity
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
``` [2](#0-1) 

These uncapped `amountDelta` values are then applied directly to balances: [3](#0-2) 

The post-execution health check provides no protection because `isHealthy()` unconditionally returns `true`: [4](#0-3) 

---

### Impact Explanation

A trader who signs a reduce-only order with `|amount| > |position|` expects the on-chain contract to cap execution to their position size. Instead, the full signed amount executes. For example:

- Trader holds a perp position of `+5` units.
- Trader signs a reduce-only sell order for `-10` units.
- `_validateOrder` caps to `-5` (non-zero → passes validation).
- `matchOrders` executes `-10` (or up to maker liquidity), flipping the position to `-5`.

The trader ends up with an unintended short position they explicitly tried to prevent. This corrupts the subaccount's position state and exposes the trader to unintended directional risk and margin requirements. The broken invariant is: **a reduce-only order must not increase the absolute position size or flip its sign**.

---

### Likelihood Explanation

Medium. The sequencer submits `MatchOrdersWithSigner` transactions and is trusted to respect reduce-only semantics off-chain. However, the on-chain contract provides no enforcement. A buggy sequencer implementation, a sequencer upgrade that mishandles reduce-only logic, or a malicious sequencer can trigger this silently. The trader has no on-chain recourse. The `filledAmounts` tracking does not prevent over-execution; it only feeds back into the next call to `_validateOrder` for the same digest. [5](#0-4) 

---

### Recommendation

Apply the reduce-only cap to the actual execution amount, not just the validation copy. The simplest fix is to compute the capped amount inside `matchOrders()` before deriving `amountDelta`, or to return the effective (capped) amount from `_validateOrder()` and use it downstream. Alternatively, enforce the reduce-only constraint as a post-condition: after computing `amountDelta`, verify that the resulting position does not exceed the pre-trade position in absolute value or flip sign.

---

### Proof of Concept

1. Trader `T` holds a perp position of `+5e18` on product `P`.
2. `T` signs a reduce-only sell order: `amount = -10e18`, `appendix` has reduce-only bit set.
3. Sequencer submits `MatchOrdersWithSigner` pairing `T`'s order against a maker with `amount = +10e18`.
4. `_validateOrder` for taker: local `order.amount` is capped to `max(-10e18, -5e18) = -5e18`; non-zero → returns `true`.
5. `matchOrders` computes `ordersInfo.taker.amountDelta = max(-10e18, -10e18) = -10e18` (original uncapped amount).
6. `_updateBalances` applies `-10e18` to `T`'s perp balance: position goes from `+5e18` to `-5e18`.
7. `T`'s position is flipped despite the reduce-only flag. The reduce-only invariant is violated on-chain with no revert. [6](#0-5) [7](#0-6)

### Citations

**File:** core/contracts/OffchainExchange.sol (L424-468)
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
```

**File:** core/contracts/OffchainExchange.sol (L625-629)
```text
    function isHealthy(
        bytes32 /* subaccount */
    ) internal view virtual returns (bool) {
        return true;
    }
```

**File:** core/contracts/OffchainExchange.sol (L745-764)
```text
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

**File:** core/contracts/OffchainExchange.sol (L811-824)
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
```

**File:** core/contracts/OffchainExchange.sol (L831-840)
```text
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
