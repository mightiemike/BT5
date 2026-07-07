### Title
Health Check Permanently Bypassed in Order Matching — (`core/contracts/OffchainExchange.sol`)

### Summary
`OffchainExchange.isHealthy` is a stub that unconditionally returns `true`, making the post-trade health checks in `matchOrders` completely inoperative. Any subaccount — regardless of collateral status — can be a taker or maker in every matched order.

### Finding Description
In `OffchainExchange.sol`, after every order match the protocol calls `isHealthy` on both the taker and maker to confirm neither party became undercollateralized: [1](#0-0) 

The function being called is: [2](#0-1) 

The body is a single `return true` with no reference to any balance, health score, or clearinghouse state. The `virtual` modifier signals intent to override, but no override exists in the deployed `OffchainExchange` contract. The `require` statements at lines 826–827 therefore always pass, making them dead guards — the exact analog of the commented-out `return shell.transfer(...)` bodies in the reference report.

### Impact Explanation
A subaccount that is already under-collateralized (maintenance health < 0) can continue to open or increase positions through the sequencer's `MatchOrders` / `MatchOrdersWithAmount` path. Each successful match updates balances in `SpotEngine` or `PerpEngine` without any post-trade solvency gate. This allows:

- Accumulation of positions that deepen insolvency before liquidation can occur.
- Socialization losses pushed onto the insurance fund and other LPs when the account is eventually finalized.
- Corruption of the protocol's core accounting invariant that every matched trade must leave both parties above their health floor. [3](#0-2) 

### Likelihood Explanation
The sequencer submits `MatchOrders` transactions continuously. Any subaccount whose health has dropped below zero — due to price moves, funding payments, or partial liquidation — will pass the `isHealthy` check on the very next order match because the check is unconditional. No special attacker capability is required; the condition is triggered by normal market operation. [4](#0-3) 

### Recommendation
Replace the stub with a real health check delegating to `clearinghouse.getHealth`:

```solidity
function isHealthy(bytes32 subaccount) internal view virtual returns (bool) {
    return IClearinghouse(clearinghouse)
        .getHealth(subaccount, IProductEngine.HealthType.INITIAL) >= 0;
}
```

If the function is intentionally left as a no-op for gas reasons, the `require` calls at lines 826–827 should be removed entirely so the dead guard does not mislead auditors and maintainers — matching the recommendation in the reference report to either restore the code or delete it.

### Proof of Concept

1. Subaccount `A` has maintenance health < 0 (e.g., after a large adverse price move).
2. Sequencer submits `MatchOrders` with `A` as taker.
3. `EndpointTx.processTransactionImpl` routes to `OffchainExchange.matchOrders`.
4. `_validateOrder` passes (signature valid, order not expired, amount non-zero).
5. Balances are updated via `_updateBalances`, deepening `A`'s insolvency.
6. `require(isHealthy(taker.order.sender), ERR_INVALID_TAKER)` evaluates `isHealthy` → `true` unconditionally.
7. Trade is committed; `A`'s negative health worsens with no revert. [2](#0-1) [1](#0-0)

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
