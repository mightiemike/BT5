### Title
Post-Trade Health Check is Non-Functional Due to `isHealthy()` Always Returning `true` — (File: `core/contracts/OffchainExchange.sol`)

---

### Summary

In `OffchainExchange.matchOrders()`, the post-balance-update health checks for both taker and maker are structurally present but completely non-functional. The `isHealthy()` function that backs those checks unconditionally returns `true`, meaning any trade that leaves either party in an unhealthy (under-collateralized) state is silently accepted on-chain.

---

### Finding Description

After computing and applying balance deltas for both sides of a matched order, `matchOrders()` calls:

```solidity
require(isHealthy(taker.order.sender), ERR_INVALID_TAKER);
require(isHealthy(maker.order.sender), ERR_INVALID_MAKER);
``` [1](#0-0) 

The function those checks delegate to is:

```solidity
function isHealthy(
    bytes32 /* subaccount */
) internal view virtual returns (bool) {
    return true;
}
``` [2](#0-1) 

`isHealthy()` is never overridden in the production `OffchainExchange` contract. The `virtual` keyword indicates the intent to override it, but no override exists in the deployed scope. The result is that the two `require` statements at lines 826–827 are dead code: they can never revert.

The balance updates that precede these checks are real and permanent:

```solidity
_updateBalances(callState, market.quoteId, taker.order.sender,
    ordersInfo.taker.amountDelta, ordersInfo.taker.quoteDelta);
_updateBalances(callState, market.quoteId, maker.order.sender,
    ordersInfo.maker.amountDelta, ordersInfo.maker.quoteDelta);
``` [3](#0-2) 

Every other balance-modifying path in the protocol performs a real post-update health check. For example, `withdrawCollateral` and `transferQuote` both call `getHealth()` after updating balances and revert if the result is negative: [4](#0-3) [5](#0-4) 

`matchOrders` is the only balance-modifying entry point that skips this enforcement entirely.

---

### Impact Explanation

A trade can leave either the taker or the maker with negative initial health (i.e., a position that exceeds the allowed leverage for the collateral posted). The protocol's core solvency invariant — that no operation should push a subaccount below initial health — is violated for every matched order. Positions opened or increased through `matchOrders` are not bounded by the margin system. This can produce immediately liquidatable positions, accumulate bad debt, and undermine the insurance fund.

---

### Likelihood Explanation

`matchOrders` is called by the sequencer via `onlyEndpoint`. The sequencer is trusted to submit well-formed transactions, but the on-chain health check exists precisely as a last-resort enforcement layer independent of sequencer behavior. With `isHealthy()` always returning `true`, that layer provides zero protection. Any order pair that the sequencer submits — whether by mistake, under operational pressure, or due to a sequencer-side bug — that results in an unhealthy post-trade state will be accepted without revert. Users can also craft signed orders with sizes that would produce negative initial health, and those orders will be matched and settled without on-chain rejection.

---

### Recommendation

Override `isHealthy()` in `OffchainExchange` (or replace the stub calls) to perform a real health check using the clearinghouse, consistent with how every other balance-modifying function in the protocol enforces post-update health:

```solidity
function isHealthy(bytes32 subaccount) internal virtual returns (bool) {
    return clearinghouse.getHealth(
        subaccount,
        IProductEngine.HealthType.INITIAL
    ) >= 0;
}
```

This mirrors the pattern already used in `Clearinghouse.withdrawCollateral`, `transferQuote`, `mintNlp`, and `burnNlp`.

---

### Proof of Concept

1. User A holds a subaccount with collateral sufficient for a 1x position.
2. User A signs a taker order for a 10x leveraged position on a perp product.
3. The sequencer submits a `MatchOrders` transaction pairing User A's order with a maker.
4. `_updateBalances()` applies the full position delta to User A's subaccount, resulting in negative initial health.
5. `require(isHealthy(taker.order.sender), ERR_INVALID_TAKER)` evaluates `isHealthy()` which returns `true` unconditionally.
6. The transaction succeeds. User A now holds a position that violates the initial margin requirement, is immediately eligible for liquidation, and represents potential bad debt to the protocol. [2](#0-1) [6](#0-5)

### Citations

**File:** core/contracts/OffchainExchange.sol (L625-629)
```text
    function isHealthy(
        bytes32 /* subaccount */
    ) internal view virtual returns (bool) {
        return true;
    }
```

**File:** core/contracts/OffchainExchange.sol (L811-827)
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
```

**File:** core/contracts/Clearinghouse.sol (L247-249)
```text
        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -toTransfer);
        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.recipient, toTransfer);
        require(_isAboveInitial(txn.sender), ERR_SUBACCT_HEALTH);
```

**File:** core/contracts/Clearinghouse.sol (L415-419)
```text
        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
```
