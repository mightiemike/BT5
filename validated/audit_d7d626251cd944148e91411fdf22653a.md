### Title
Unbounded `NlpLockedBalanceQueue` Iteration Permanently Locks User NLP Funds - (`core/contracts/SpotEngineState.sol`)

---

### Summary

`tryUnlockNlpBalance` iterates through every accumulated entry in a per-user `NlpLockedBalanceQueue` in a single unbounded `while` loop. Because each NLP deposit at a distinct oracle timestamp appends a new queue entry, a user who makes many deposits across different blocks accumulates a queue that, once all entries pass the 4-day lock period, must be fully traversed in one call. When the queue is large enough, every subsequent NLP operation (`mintNlp`, `burnNlp`) reverts due to gas exhaustion, permanently locking the user's NLP tokens.

---

### Finding Description

`tryUnlockNlpBalance` in `SpotEngineState.sol` processes all matured lock entries in a single `while` loop:

```solidity
// SpotEngineState.sol lines 292–303
while (
    queue.unlockedUpTo < queue.balanceCount &&
    queue.balances[queue.unlockedUpTo].unlockedAt <= getOracleTime()
) {
    queue.unlockedBalanceSum.amount += queue.balances[queue.unlockedUpTo].balance.amount;
    delete queue.balances[queue.unlockedUpTo];
    queue.unlockedUpTo++;
}
```

The queue grows in `handleNlpLockedBalance`. A new entry is appended whenever the last entry's `unlockedAt` does not exactly match `getOracleTime() + NLP_LOCK_PERIOD`:

```solidity
// SpotEngine.sol lines 152–167
if (
    queue.balanceCount > 0 &&
    queue.balances[queue.balanceCount - 1].unlockedAt == getOracleTime() + NLP_LOCK_PERIOD
) {
    queue.balances[queue.balanceCount - 1].balance.amount += amountDelta;
} else {
    queue.balances[queue.balanceCount] = NlpLockedBalance({...});
    queue.balanceCount++;
}
```

Because `getOracleTime()` advances with each block, any two deposits in different blocks produce different `unlockedAt` values and therefore different queue entries. `NLP_LOCK_PERIOD` is 4 days (345,600 seconds). A user making one deposit per block over 4 days accumulates up to ~172,800 entries, all of which unlock simultaneously after the lock period expires.

`tryUnlockNlpBalance` is called unconditionally at the start of `handleNlpLockedBalance` (line 147 of `SpotEngine.sol`) and inside `getNlpUnlockedBalance` (line 133 of `SpotEngine.sol`). Both `mintNlp` and `burnNlp` in `Clearinghouse.sol` trigger these paths:

- `burnNlp` calls `spotEngine.getNlpUnlockedBalance(txn.sender)` (line 499) and then `spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, -nlpAmount)` (line 511), each of which invokes `tryUnlockNlpBalance`.
- `mintNlp` calls `spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, nlpAmount)` (line 473), which also invokes `tryUnlockNlpBalance`.

There is no mechanism to process the queue incrementally across multiple transactions. Once the queue exceeds the block gas limit per iteration, all NLP operations for that subaccount revert permanently.

---

### Impact Explanation

A user who has made many NLP deposits at distinct oracle timestamps and then waits for the 4-day lock period to expire will find that every subsequent `mintNlp` and `burnNlp` transaction reverts due to gas exhaustion. Their NLP tokens are permanently inaccessible within the protocol. Each `while` loop iteration costs approximately 3 SSTOREs + 1 SLOAD (~15,000–20,000 gas). With a 30M block gas limit, roughly 1,500–2,000 queue entries are sufficient to cause a permanent DoS. A user making one deposit every ~2–3 minutes over 4 days reaches this threshold through normal usage.

---

### Likelihood Explanation

Any user who participates in NLP liquidity provision with frequent deposits (e.g., automated strategies, regular rebalancing) will naturally accumulate queue entries at the rate of one per block in which they deposit. The condition is triggered without any adversarial action — normal protocol usage over 4 days is sufficient. The impact is permanent once triggered, as there is no partial-processing escape hatch.

---

### Recommendation

Process the queue lazily with a bounded iteration cap per call, and track a `processedUpTo` cursor so subsequent calls resume where the previous left off. Alternatively, batch entries by day or by a coarser time granularity (e.g., round `unlockedAt` to the nearest hour) to reduce queue growth rate. A hard cap on `balanceCount` per subaccount with a merge-or-reject policy would also bound the worst case.

---

### Proof of Concept

1. User submits 2,000 `MintNlp` transactions, each in a separate block (different oracle timestamps).
2. Each call to `handleNlpLockedBalance` appends a new entry because `getOracleTime()` differs between blocks, so the merge condition `queue.balances[queue.balanceCount - 1].unlockedAt == getOracleTime() + NLP_LOCK_PERIOD` is false. `queue.balanceCount` reaches 2,000.
3. User waits 4 days (`NLP_LOCK_PERIOD`). All 2,000 entries now satisfy `unlockedAt <= getOracleTime()`.
4. User submits a `BurnNlp` transaction. `burnNlp` calls `spotEngine.getNlpUnlockedBalance(txn.sender)` → `tryUnlockNlpBalance`. The `while` loop must iterate 2,000 times, each iteration performing multiple SSTOREs. Total gas exceeds the block gas limit. Transaction reverts.
5. User retries with a smaller `nlpAmount` — same result, because `tryUnlockNlpBalance` processes the entire queue regardless of burn amount.
6. User attempts `MintNlp` to trigger incremental processing — same revert, because `handleNlpLockedBalance` also calls `tryUnlockNlpBalance` unconditionally.
7. User's NLP balance is permanently inaccessible. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** core/contracts/SpotEngineState.sol (L285-306)
```text
    function tryUnlockNlpBalance(bytes32 subaccount)
        public
        returns (Balance memory)
    {
        NlpLockedBalanceQueue storage queue = nlpLockedBalanceQueues[
            subaccount
        ];
        while (
            queue.unlockedUpTo < queue.balanceCount &&
            queue.balances[queue.unlockedUpTo].unlockedAt <= getOracleTime()
        ) {
            // we can unlock this balance
            queue.unlockedBalanceSum.amount += queue
                .balances[queue.unlockedUpTo]
                .balance
                .amount;
            delete queue.balances[queue.unlockedUpTo];
            queue.unlockedUpTo++;
        }

        return queue.unlockedBalanceSum;
    }
```

**File:** core/contracts/SpotEngine.sol (L139-147)
```text
    function handleNlpLockedBalance(bytes32 subaccount, int128 amountDelta)
        internal
    {
        _assertInternal();

        // N_ACCOUNT is not limited by lock period
        if (subaccount == N_ACCOUNT) return;

        tryUnlockNlpBalance(subaccount);
```

**File:** core/contracts/SpotEngine.sol (L148-167)
```text
        if (amountDelta > 0) {
            NlpLockedBalanceQueue storage queue = nlpLockedBalanceQueues[
                subaccount
            ];
            if (
                queue.balanceCount > 0 &&
                queue.balances[queue.balanceCount - 1].unlockedAt ==
                getOracleTime() + NLP_LOCK_PERIOD
            ) {
                queue
                    .balances[queue.balanceCount - 1]
                    .balance
                    .amount += amountDelta;
            } else {
                queue.balances[queue.balanceCount] = NlpLockedBalance({
                    balance: Balance({amount: amountDelta}),
                    unlockedAt: getOracleTime() + NLP_LOCK_PERIOD
                });
                queue.balanceCount++;
            }
```

**File:** core/contracts/Clearinghouse.sol (L496-512)
```text
        require(txn.nlpAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        int128 nlpAmount = int128(txn.nlpAmount);
        require(
            spotEngine.getNlpUnlockedBalance(txn.sender).amount >= nlpAmount,
            ERR_UNLOCKED_NLP_INSUFFICIENT
        );
        int128 quoteAmount = nlpAmount.mul(oraclePriceX18);
        int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);
        quoteAmount = MathHelper.max(0, quoteAmount - burnFee);

        _validateNlpRebalance(nlpPools, nlpPoolRebalanceX18, -quoteAmount);
        for (uint128 i = 0; i < nlpPoolRebalanceX18.length; i++) {
            require(nlpPoolRebalanceX18[i] <= 0, ERR_INVALID_NLP_REBALANCE);
        }

        spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, -nlpAmount);
        spotEngine.updateBalance(NLP_PRODUCT_ID, N_ACCOUNT, nlpAmount);
```

**File:** core/contracts/common/Constants.sol (L52-52)
```text
uint64 constant NLP_LOCK_PERIOD = 4 * 24 * 60 * 60; // 4 days
```
