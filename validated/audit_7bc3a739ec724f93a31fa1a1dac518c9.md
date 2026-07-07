### Title
Post-Trade Health Check Permanently Bypassed via `isHealthy` Stub Always Returning `true` — (File: `core/contracts/OffchainExchange.sol`)

---

### Summary

`OffchainExchange::isHealthy` is a stub that unconditionally returns `true`. The `matchOrders` function calls `require(isHealthy(...))` after applying balance deltas to both taker and maker, but because the function never returns `false`, the post-trade health gate is permanently open. Any trade that would leave a subaccount below its initial margin threshold executes without reversion.

---

### Finding Description

In `OffchainExchange.sol`, the health-gate function is defined as:

```solidity
function isHealthy(
    bytes32 /* subaccount */
) internal view virtual returns (bool) {
    return true;
}
``` [1](#0-0) 

After all balance updates are applied in `matchOrders`, the protocol attempts to enforce post-trade health:

```solidity
require(isHealthy(taker.order.sender), ERR_INVALID_TAKER);
require(isHealthy(maker.order.sender), ERR_INVALID_MAKER);
``` [2](#0-1) 

Because `isHealthy` always returns `true`, both `require` statements are unconditionally satisfied. No subclass in the production contracts directory overrides this function.



The analog to the external report is direct: in the Remora finding, `exchangeAllowed` returns `false` but the caller ignores the return value; here, `isHealthy` is the boolean-returning gate, and the gate is permanently held open by the stub body — the functional outcome is identical: the authorization check never blocks execution.

---

### Impact Explanation

After `_updateBalances` credits/debits both sides of a matched order, there is no effective check that either subaccount remains above its initial margin threshold. A sequencer-submitted trade can drive a subaccount's health arbitrarily negative. This corrupts the solvency invariant that the clearinghouse relies on: `getHealth(subaccount, INITIAL) >= 0` is enforced on withdrawals and quote transfers but not on trade settlement, creating an asymmetry that can be exploited to open positions far beyond available collateral. [3](#0-2) 

---

### Likelihood Explanation

Every call to `matchOrders` — the primary settlement path for all spot and perp trades — passes through the broken health gate. The sequencer submits these transactions on behalf of users; a user who signs an order that would breach their margin limit will have that order matched and settled without reversion. The trigger is reachable on every trade execution. [4](#0-3) 

---

### Recommendation

Replace the stub with a real health check that queries the clearinghouse:

```solidity
function isHealthy(bytes32 subaccount) internal virtual returns (bool) {
    return clearinghouse.getHealth(subaccount, IProductEngine.HealthType.INITIAL) >= 0;
}
```

Alternatively, inline the `require(getHealth(...) >= 0, ERR_SUBACCT_HEALTH)` call directly in `matchOrders`, consistent with how `withdrawCollateral` and `transferQuote` enforce health. [5](#0-4) 

---

### Proof of Concept

1. User A signs a taker order for product P with `amount` large enough that, after matching, their initial health would be negative.
2. Sequencer calls `Endpoint::submitTransactionsChecked` → `processTransaction` → `OffchainExchange::matchOrders`.
3. `_updateBalances` applies the base and quote deltas to User A's subaccount.
4. `require(isHealthy(taker.order.sender), ERR_INVALID_TAKER)` evaluates `isHealthy(...)` → `true` unconditionally.
5. The transaction succeeds; User A's subaccount now has `getHealth(..., INITIAL) < 0`, violating the solvency invariant enforced everywhere else in the protocol. [1](#0-0) [2](#0-1)

### Citations

**File:** core/contracts/OffchainExchange.sol (L625-629)
```text
    function isHealthy(
        bytes32 /* subaccount */
    ) internal view virtual returns (bool) {
        return true;
    }
```

**File:** core/contracts/OffchainExchange.sol (L631-635)
```text
    function matchOrders(IEndpoint.MatchOrdersWithSigner calldata txn)
        external
        onlyEndpoint
    {
        CallState memory callState = _getCallState(txn.matchOrders.productId);
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

**File:** core/contracts/Clearinghouse.sol (L639-642)
```text
    function _isAboveInitial(bytes32 subaccount) internal returns (bool) {
        // Weighted initial health with limit orders < 0
        return getHealth(subaccount, IProductEngine.HealthType.INITIAL) >= 0;
    }
```
