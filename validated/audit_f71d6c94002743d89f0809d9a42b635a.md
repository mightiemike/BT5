### Title
Post-Trade Health Check Stub Unconditionally Returns True, Disabling Collateral Enforcement After Order Matching — (`core/contracts/OffchainExchange.sol`)

---

### Summary

`OffchainExchange.isHealthy` is a stub function that always returns `true`, completely disabling the post-trade health check that is supposed to prevent order matching from leaving subaccounts undercollateralized. This is a direct analog to the external report's pattern: a placeholder function that bypasses a critical validation gate.

---

### Finding Description

In `OffchainExchange.matchOrders`, after all balance updates for taker and maker are applied, the contract enforces two post-trade health checks:

```solidity
require(isHealthy(taker.order.sender), ERR_INVALID_TAKER);
require(isHealthy(maker.order.sender), ERR_INVALID_MAKER);
``` [1](#0-0) 

These calls are the on-chain last line of defense ensuring that neither party is left below their initial health threshold after a trade. However, the function they call is:

```solidity
function isHealthy(
    bytes32 /* subaccount */
) internal view virtual returns (bool) {
    return true;
}
``` [2](#0-1) 

This is a stub — identical in structure to the `AuctionTimeControl.sol` stubs in the external report — that unconditionally returns `true` regardless of the subaccount's actual collateral state. The `virtual` keyword indicates it was intended to be overridden with real logic, but the deployed `OffchainExchange` contract provides no override.

By contrast, the rest of the protocol correctly enforces health checks. For example, `Clearinghouse._isAboveInitial` calls `getHealth` with the real engine state:

```solidity
function _isAboveInitial(bytes32 subaccount) internal returns (bool) {
    return getHealth(subaccount, IProductEngine.HealthType.INITIAL) >= 0;
}
``` [3](#0-2) 

The same pattern is used in `withdrawCollateral`, `transferQuote`, `mintNlp`, and `burnNlp` — all of which call `getHealth` directly. The `matchOrders` path is the sole exception, and it is silenced by the stub.

---

### Impact Explanation

The broken invariant is: *after a trade is matched, both the taker and maker must remain at or above their initial health threshold.* With `isHealthy` always returning `true`, this invariant is never enforced on-chain for any matched order.

Concretely:
- A trade can be matched that drives a subaccount's collateral below its initial margin requirement.
- The protocol accumulates undercollateralized positions without on-chain rejection.
- This corrupts the solvency accounting of the `Clearinghouse`, since positions that should be blocked are silently accepted.
- Undercollateralized subaccounts become candidates for liquidation, but if the price moves further against them before liquidation, the insurance fund absorbs the shortfall — draining `insurance` and ultimately socializing losses.

The corrupted state variable is the subaccount's spot/perp balance in `SpotEngine`/`PerpEngine`, which is updated by `_updateBalances` before the disabled health check. [4](#0-3) 

---

### Likelihood Explanation

The entry path is `Endpoint.submitTransactionsChecked` → `processTransaction` → `EndpointTx.processTransactionImpl` (via `delegatecall`) → `OffchainExchange.matchOrders`. This path requires `msg.sender == sequencer`. [5](#0-4) 

The sequencer is a trusted but not infallible actor. The post-trade health check exists precisely as an on-chain safety net that the sequencer cannot bypass — it is the protocol's enforcement layer independent of sequencer behavior. With the stub in place, that layer is entirely absent. Any sequencer error, latency in price feeds, or deliberate submission of a marginal order can result in an undercollateralized position being accepted on-chain with no rejection.

---

### Recommendation

Replace the stub with a real health check using the clearinghouse, consistent with how health is enforced elsewhere in the protocol:

```solidity
function isHealthy(bytes32 subaccount) internal virtual returns (bool) {
    return IClearinghouse(clearinghouse).getHealth(
        subaccount,
        IProductEngine.HealthType.INITIAL
    ) >= 0;
}
```

This mirrors the pattern already used in `Clearinghouse._isAboveInitial` and ensures post-trade collateral enforcement is active in production.

---

### Proof of Concept

1. Sequencer submits a `MatchOrders` transaction pairing a taker order that, after execution, leaves the taker's subaccount below initial health.
2. `matchOrders` calls `_updateBalances`, writing the new undercollateralized state to `SpotEngine`/`PerpEngine`.
3. `require(isHealthy(taker.order.sender), ERR_INVALID_TAKER)` is reached.
4. `isHealthy` returns `true` unconditionally — the require passes.
5. The trade is finalized. The taker's subaccount is now undercollateralized on-chain with no revert.
6. The protocol's solvency invariant is violated; the position is eligible for liquidation, and any shortfall falls on the insurance fund. [2](#0-1) [1](#0-0)

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

**File:** core/contracts/Clearinghouse.sol (L639-642)
```text
    function _isAboveInitial(bytes32 subaccount) internal returns (bool) {
        // Weighted initial health with limit orders < 0
        return getHealth(subaccount, IProductEngine.HealthType.INITIAL) >= 0;
    }
```

**File:** core/contracts/Endpoint.sol (L278-293)
```text
        validateSubmissionIdx(idx);
        require(msg.sender == sequencer);
        // TODO: if one of these transactions fails this means the sequencer is in an error state
        // we should probably record this, and engage some sort of recovery mode

        bytes32 digest = keccak256(abi.encode(idx));
        for (uint256 i = 0; i < transactions.length; ++i) {
            digest = keccak256(abi.encodePacked(digest, transactions[i]));
        }
        verifier.requireValidSignature(digest, e, s, signerBitmask);

        for (uint256 i = 0; i < transactions.length; i++) {
            bytes calldata transaction = transactions[i];
            processTransaction(transaction);
            nSubmissions += 1;
        }
```
