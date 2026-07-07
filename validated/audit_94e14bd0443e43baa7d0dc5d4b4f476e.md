### Title
Off-by-One in `forceRebalanceNlpPool` Health Check Loop Silently Skips Pool 0 Solvency Validation — (File: `core/contracts/Clearinghouse.sol`)

---

### Summary

`forceRebalanceNlpPool` in `Clearinghouse.sol` applies balance changes to **all** NLP pool subaccounts (indices 0 through n-1) via `_applyNlpRebalance`, but the subsequent post-rebalance health check loop starts at `i = 1`, silently skipping the solvency check for `nlpPools[0]`. This is a direct structural analog to the email address bug: just as `get_email_address` finds only the **last** angle bracket and validates only the last recipient while silently skipping all previous ones, `forceRebalanceNlpPool` validates pools 1 through n-1 and silently skips pool 0.

---

### Finding Description

In `Clearinghouse.sol`, `forceRebalanceNlpPool` is designed to perform a zero-sum rebalance across NLP pool subaccounts and then assert that every pool remains solvent. The balance application step is correct — `_applyNlpRebalance` iterates from `i = 0`: [1](#0-0) 

However, the health check loop that follows starts at `i = 1`: [2](#0-1) 

`nlpPools[0].subaccount` receives its balance delta from `_applyNlpRebalance` but is **never** passed to `getHealth`. A rebalance that drains pool 0 below zero initial health will pass all `require` checks and be committed to state without any revert.

The full function: [3](#0-2) 

---

### Impact Explanation

Pool 0's subaccount can be left with **negative initial health** after a `forceRebalanceNlpPool` call. This directly corrupts the core solvency invariant the clearinghouse enforces for every other operation. The unhealthy subaccount persists in state because the missing check allows it to be committed. Downstream effects include:

- The pool 0 subaccount holds a negative balance that the clearinghouse would normally reject in any other context.
- NLP redemptions, profit-share distributions, and liquidation logic that interact with pool 0 operate on a corrupted balance.
- The `_validateNlpRebalance` check only verifies that the sum of rebalance amounts equals zero — it does not prevent pool 0 from being drained. [4](#0-3) 

---

### Likelihood Explanation

`ForceRebalanceNlpPool` is submitted as a slow mode transaction that requires `sender == owner()`: [5](#0-4) 

The owner may submit a legitimate rebalance that moves funds from pool 0 to other pools without realizing that pool 0's health is never checked. The bug is entirely silent — no revert occurs, no event signals the missing check, and the corrupted state is committed. The off-by-one is a latent defect that activates whenever pool 0 is the net sender in a rebalance.

---

### Recommendation

Change the loop start index from `1` to `0` so that every pool's health is validated after the rebalance:

```solidity
// Before (buggy):
for (uint128 i = 1; i < nlpPools.length; i++) {

// After (fixed):
for (uint128 i = 0; i < nlpPools.length; i++) {
``` [2](#0-1) 

---

### Proof of Concept

1. Owner submits a `ForceRebalanceNlpPool` slow mode transaction with:
   - `nlpPools = [pool0, pool1]`
   - `nlpPoolRebalanceX18 = [-X, +X]` where `X` is large enough to push pool 0 below zero initial health.
2. `_validateNlpRebalance` passes: sum of `[-X, +X]` equals the required `deltaQuoteAmount = 0`.
3. `_applyNlpRebalance` applies `-X` to `pool0.subaccount` and `+X` to `pool1.subaccount`.
4. The health check loop runs only for `i = 1` (pool1), which is healthy after receiving `+X`.
5. Pool 0's health is **never checked**; it is left with negative initial health.
6. The transaction succeeds; pool 0 is now insolvent with no protocol-level rejection. [3](#0-2)

### Citations

**File:** core/contracts/Clearinghouse.sol (L423-437)
```text
    function _validateNlpRebalance(
        IEndpoint.NlpPool[] calldata nlpPools,
        int128[] calldata nlpPoolRebalanceX18,
        int128 deltaQuoteAmount
    ) internal pure {
        require(
            nlpPools.length == nlpPoolRebalanceX18.length,
            ERR_INVALID_NLP_REBALANCE
        );
        int128 rebalanceAmount = 0;
        for (uint128 i = 0; i < nlpPoolRebalanceX18.length; i++) {
            rebalanceAmount += nlpPoolRebalanceX18[i];
        }
        require(deltaQuoteAmount == rebalanceAmount, ERR_INVALID_NLP_REBALANCE);
    }
```

**File:** core/contracts/Clearinghouse.sol (L439-451)
```text
    function _applyNlpRebalance(
        ISpotEngine spotEngine,
        IEndpoint.NlpPool[] calldata nlpPools,
        int128[] calldata nlpPoolRebalanceX18
    ) internal {
        for (uint128 i = 0; i < nlpPoolRebalanceX18.length; i++) {
            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                nlpPools[i].subaccount,
                nlpPoolRebalanceX18[i]
            );
        }
    }
```

**File:** core/contracts/Clearinghouse.sol (L532-549)
```text
    function forceRebalanceNlpPool(
        IEndpoint.NlpPool[] calldata nlpPools,
        int128[] calldata nlpPoolRebalanceX18
    ) external onlyEndpoint {
        _validateNlpRebalance(nlpPools, nlpPoolRebalanceX18, 0);
        ISpotEngine spotEngine = _spotEngine();
        _applyNlpRebalance(spotEngine, nlpPools, nlpPoolRebalanceX18);

        for (uint128 i = 1; i < nlpPools.length; i++) {
            require(
                getHealth(
                    nlpPools[i].subaccount,
                    IProductEngine.HealthType.INITIAL
                ) >= 0,
                ERR_SUBACCT_HEALTH
            );
        }
    }
```

**File:** core/contracts/EndpointTx.sol (L355-368)
```text
        } else if (
            txType == IEndpoint.TransactionType.WithdrawInsurance ||
            txType == IEndpoint.TransactionType.DelistProduct ||
            txType == IEndpoint.TransactionType.DumpFees ||
            txType == IEndpoint.TransactionType.RebalanceXWithdraw ||
            txType == IEndpoint.TransactionType.UpdateTierFeeRates ||
            txType == IEndpoint.TransactionType.AddNlpPool ||
            txType == IEndpoint.TransactionType.UpdateNlpPool ||
            txType == IEndpoint.TransactionType.DeleteNlpPool ||
            txType == IEndpoint.TransactionType.ForceRebalanceNlpPool ||
            txType == IEndpoint.TransactionType.NlpProfitShare ||
            txType == IEndpoint.TransactionType.UpdateBuilder
        ) {
            require(sender == owner());
```
