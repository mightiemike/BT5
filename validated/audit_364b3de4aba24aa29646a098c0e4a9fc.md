### Title
Unbounded Loop Over All Spot Product IDs in `claimBuilderFee` Permanently Locks Builder Fees - (File: `core/contracts/OffchainExchange.sol`)

---

### Summary

`OffchainExchange.claimBuilderFee` iterates over every registered spot product ID without pagination. As the protocol lists more spot products over time, this loop will eventually exceed the block gas limit, causing the slow mode transaction to permanently revert via the OOG guard in `Endpoint._executeSlowModeTransaction`, and permanently locking all accrued builder fees in the contract.

---

### Finding Description

`claimBuilderFee` fetches the full `spotEngine.getProductIds()` array and iterates over every element unconditionally: [1](#0-0) 

```solidity
uint32[] memory productIds = spotEngine.getProductIds();
for (uint32 i = 0; i < productIds.length; i++) {
    uint32 productId = productIds[i];
    int128 collectedFee = collectedBuilderFee[productId][builderId];
    if (collectedFee == 0) {
        continue;
    }
    emit ClaimBuilderFee(builderId, productId, sender, collectedFee);
    spotEngine.updateBalance(productId, sender, collectedFee);
    collectedBuilderFee[productId][builderId] = 0;
}
```

Every iteration performs a storage read (`collectedBuilderFee[productId][builderId]`) and, when non-zero, an external call to `spotEngine.updateBalance`. Both are expensive operations. The array length grows monotonically as the protocol adds spot products; there is no mechanism to remove products from the list.

The function is reached through the slow mode queue:

1. A builder owner calls `Endpoint.submitSlowModeTransaction` with a `ClaimBuilderFee` payload. [2](#0-1) 
2. The sequencer (or the builder owner after the delay) calls `Endpoint.executeSlowModeTransaction`. [3](#0-2) 
3. `_executeSlowModeTransaction` wraps the inner call in a `try/catch` with an OOG guard: if `gasleft() <= 250000 || gasleft() <= gasRemaining / 2`, it executes `invalid()`, reverting the **entire outer transaction**. [4](#0-3) 

Because the outer transaction reverts, the slow mode entry is **not** consumed from the queue. The builder can resubmit, but every attempt will hit the same OOG condition once the product list is large enough. The fees remain in `collectedBuilderFee` storage but are unreachable.

---

### Impact Explanation

Builder fees are real protocol revenue credited to builder-owned subaccounts. Once the product list crosses the gas threshold, every `ClaimBuilderFee` slow mode transaction will permanently revert. All accumulated `collectedBuilderFee[productId][builderId]` balances become permanently inaccessible — a concrete, irreversible asset loss for every registered builder.

---

### Likelihood Explanation

The Nado protocol is designed to list many spot products over time. Each new spot product added by the owner increases the loop length. No product removal mechanism exists in `SpotEngineState`. The threshold is not immediate, but it is deterministic and inevitable as the protocol scales. Any builder who has accrued fees at that point suffers permanent loss.

---

### Recommendation

Replace the full-scan loop with a caller-supplied list of `productId` values to claim, or add `startIndex`/`endIndex` pagination parameters analogous to the BondAggregator fix. Only iterate over products where the builder actually has non-zero fees, or maintain a per-builder set of product IDs with non-zero balances.

---

### Proof of Concept

1. Protocol lists N spot products (N large enough that iterating over them in one transaction exceeds ~30M gas).
2. Builder owner calls `endpoint.submitSlowModeTransaction(abi.encodePacked(uint8(TransactionType.ClaimBuilderFee), abi.encode(ClaimBuilderFee({sender: builderSubaccount, builderId: id}))))`.
3. After the slow mode delay, builder owner calls `endpoint.executeSlowModeTransaction()`.
4. `_executeSlowModeTransaction` calls `this.processSlowModeTransaction(...)` which delegates to `claimBuilderFee`.
5. The inner call runs out of gas iterating over all N product IDs.
6. The `catch` block detects `gasleft() <= gasRemaining / 2` and executes `assembly { invalid() }`, reverting the outer transaction. [5](#0-4) 
7. The slow mode entry is not deleted. The builder resubmits and the cycle repeats indefinitely.
8. All `collectedBuilderFee` balances for this builder are permanently locked.

### Citations

**File:** core/contracts/OffchainExchange.sol (L878-888)
```text
        uint32[] memory productIds = spotEngine.getProductIds();
        for (uint32 i = 0; i < productIds.length; i++) {
            uint32 productId = productIds[i];
            int128 collectedFee = collectedBuilderFee[productId][builderId];
            if (collectedFee == 0) {
                continue;
            }
            emit ClaimBuilderFee(builderId, productId, sender, collectedFee);
            spotEngine.updateBalance(productId, sender, collectedFee);
            collectedBuilderFee[productId][builderId] = 0;
        }
```

**File:** core/contracts/EndpointTx.sol (L316-327)
```text
        } else if (txType == IEndpoint.TransactionType.ClaimBuilderFee) {
            IEndpoint.ClaimBuilderFee memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.ClaimBuilderFee)
            );
            validateSender(txn.sender, sender);
            requireSubaccount(txn.sender);
            IOffchainExchange(offchainExchange).claimBuilderFee(
                txn.sender,
                txn.builderId
            );
        } else {
```

**File:** core/contracts/Endpoint.sol (L205-227)
```text
            uint256 gasRemaining = gasleft();
            // solhint-disable-next-line no-empty-blocks
            try this.processSlowModeTransaction(txn.sender, txn.tx) {} catch {
                // we need to differentiate between a revert and an out of gas
                // the issue is that in evm every inner call only 63/64 of the
                // remaining gas in the outer frame is forwarded. as a result
                // the amount of gas left for execution is (63/64)**len(stack)
                // and you can get an out of gas while spending an arbitrarily
                // low amount of gas in the final frame. we use a heuristic
                // here that isn't perfect but covers our cases.
                // having gasleft() <= gasRemaining / 2 buys us 44 nested calls
                // before we miss out of gas errors; 1/2 ~= (63/64)**44
                // this is good enough for our purposes

                if (gasleft() <= 250000 || gasleft() <= gasRemaining / 2) {
                    // solhint-disable-next-line no-inline-assembly
                    assembly {
                        invalid()
                    }
                }

                // try return funds now removed
            }
```

**File:** core/contracts/Endpoint.sol (L231-236)
```text
    function executeSlowModeTransaction() external {
        SlowModeConfig memory _slowModeConfig = slowModeConfig;
        _executeSlowModeTransaction(_slowModeConfig, false);
        nSubmissions += 1;
        slowModeConfig = _slowModeConfig;
    }
```
