### Title
Reduce-Only Order Execution Uses Uncapped Amount, Allowing Position Flip — (`core/contracts/OffchainExchange.sol`)

---

### Summary

In `OffchainExchange.sol`, the `_validateOrder` function correctly caps a reduce-only order's amount to the user's current position size to prevent position flips. However, this capping is applied only to a **local copy** of the order struct, not to the original order used for execution in `matchOrders`. As a result, reduce-only orders execute with the full original (uncapped) amount, violating the reduce-only invariant and potentially flipping a user's position.

---

### Finding Description

In `_validateOrder`, a local copy of the order is created at line 424:

```solidity
IEndpoint.Order memory order = signedOrder.order;
```

The reduce-only capping logic (lines 438–455) then modifies `order.amount` on this local copy:

```solidity
if (_isReduceOnly(order.appendix)) {
    int128 amount = callState.isPerp
        ? callState.perp.getBalance(callState.productId, order.sender).amount
        : callState.spot.getBalance(callState.productId, order.sender).amount;
    if ((order.amount > 0) == (amount > 0)) {
        order.amount = 0;
    } else if (order.amount > 0) {
        order.amount = MathHelper.min(order.amount, -amount);
    } else if (