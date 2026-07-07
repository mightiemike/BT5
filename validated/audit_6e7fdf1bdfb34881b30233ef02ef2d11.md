### Title
Unbounded `tryUnlockNlpBalance` Loop Causes OOG DoS on NLP Interactions — (`core/contracts/SpotEngineState.sol`)

---

### Summary

`SpotEngine` tracks NLP token deposits per subaccount in a `NlpLockedBalanceQueue`. Each deposit at a distinct oracle timestamp appends a new entry to the queue. The `tryUnlockNlpBalance` function iterates through all unlocked entries in a `while` loop, performing storage writes per iteration. Because there is no cap on queue length and the loop is called on every NLP balance update, a user who accumulates enough queue entries can cause every subsequent NLP interaction to revert with OOG, permanently locking their NLP position.

---

### Finding Description

`NlpLockedBalanceQueue` is defined in `ISpotEngine` as:

```solidity
struct NlpLockedBalanceQueue {
    mapping(uint64 => NlpLockedBalance) balances;
    uint64 balanceCount;
    uint64 unlockedUpTo;
    Balance unlockedBalanceSum;
}
``` [1](#0-0) 

Each time a user deposits NLP tokens, `handleNlpLockedBalance` is called. It merges with the last entry only if the oracle timestamp is identical; otherwise it appends a new entry and increments `balanceCount`:

```solidity
queue.balances[queue.balanceCount] = NlpLockedBalance({...});
queue.balanceCount++;
``` [2](#0-1) 

A user depositing NLP across N distinct oracle timestamps (i.e., N different blocks) accumulates N entries. After `NLP_LOCK_PERIOD` elapses, all N entries become eligible for processing.

`tryUnlockNlpBalance` then iterates through every eligible entry:

```solidity
while (
    queue.unlockedUpTo < queue.balanceCount &&
    queue.balances[queue.unlockedUpTo].unlockedAt <= getOracleTime()
) {
    queue.unlockedBalanceSum.amount += queue.balances[queue.unlockedUpTo].balance.amount;
    delete queue.balances[queue.unlockedUpTo];
    queue.unlockedUpTo++;
}
``` [3](#0-2) 

Each iteration performs at least two storage writes (`delete` + `unlockedUpTo++`) and one storage read, costing thousands of gas. With enough entries this loop exceeds the block gas limit.

This loop is invoked unconditionally inside `handleNlpLockedBalance`, which is called by both `updateBalance` overloads whenever `productId == NLP_PRODUCT_ID`:

```solidity
if (productId == NLP_PRODUCT_ID) {
    handleNlpLockedBalance(subaccount, amountDelta);
}
``` [4](#0-3) [5](#0-4) 

`updateBalance` is called by the clearinghouse and offchain exchange on every NLP trade, deposit, or withdrawal. Once the loop OOGs, every NLP interaction for that subaccount reverts permanently.

---

### Impact Explanation

A subaccount that has accumulated a large `NlpLockedBalanceQueue` will have every subsequent NLP `updateBalance` call revert with OOG. This permanently blocks:
- NLP withdrawals (user cannot redeem their NLP tokens)
- Further NLP deposits
- Any settlement or liquidation path that touches the NLP balance

The user's NLP tokens become permanently inaccessible — a definite loss of funds with no recovery path, since the only way to clear the queue is through the same OOG-reverting loop.

---

### Likelihood Explanation

Any unprivileged user can trigger this by making NLP deposits across many distinct blocks. There is no cap on `balanceCount` (it is `uint64`). The merging guard only fires when two consecutive deposits share the exact same oracle timestamp, which is unlikely across separate transactions. A user making one deposit per block over a sustained period accumulates one entry per block. The number of entries required to OOG depends on the block gas limit of the deployed chain, but given each iteration costs ~10,000–20,000 gas (two cold-to-warm storage writes + delete refund), a few hundred to a few thousand entries suffice.

---

### Recommendation

1. **Cap `balanceCount`**: Enforce a maximum number of locked balance entries per subaccount (e.g., 100). Reject new NLP deposits if the cap is reached.
2. **Batch-limit the loop**: Process at most a fixed number of entries per call (e.g., 50), and allow the caller to invoke `tryUnlockNlpBalance` multiple times to drain the queue incrementally.
3. **Merge aggressively**: Instead of only merging with the last entry at the same timestamp, consider merging all entries with the same `unlockedAt` bucket (e.g., rounded to a day).

---

### Proof of Concept

1. Attacker subaccount calls `depositCollateralWithReferral` (or equivalent NLP deposit path) once per block for N blocks, each at a distinct oracle timestamp. This creates N entries in `nlpLockedBalanceQueues[subaccount]` with `balanceCount = N`.
2. Attacker waits for `NLP_LOCK_PERIOD` to elapse so all N entries satisfy `unlockedAt <= getOracleTime()`.
3. Attacker (or anyone) calls any function that triggers `updateBalance(NLP_PRODUCT_ID, subaccount, ...)` — e.g., an NLP withdrawal.
4. `handleNlpLockedBalance` → `tryUnlockNlpBalance` iterates all N entries. Each iteration executes `delete queue.balances[i]` (storage zero-write) + `queue.unlockedUpTo++` (storage write) + `queue.unlockedBalanceSum.amount +=` (storage write) ≈ 15,000–20,000 gas per iteration.
5. For N ≈ 500–1000 entries, total gas ≈ 7.5M–20M, exceeding typical EVM block gas limits.
6. The transaction reverts with OOG. All future NLP interactions for this subaccount also revert. The user's NLP balance is permanently locked. [6](#0-5) [7](#0-6)

### Citations

**File:** core/contracts/interfaces/engine/ISpotEngine.sol (L53-58)
```text
    struct NlpLockedBalanceQueue {
        mapping(uint64 => NlpLockedBalance) balances;
        uint64 balanceCount;
        uint64 unlockedUpTo;
        Balance unlockedBalanceSum;
    }
```

**File:** core/contracts/SpotEngine.sol (L139-174)
```text
    function handleNlpLockedBalance(bytes32 subaccount, int128 amountDelta)
        internal
    {
        _assertInternal();

        // N_ACCOUNT is not limited by lock period
        if (subaccount == N_ACCOUNT) return;

        tryUnlockNlpBalance(subaccount);
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
        } else if (amountDelta < 0) {
            Balance memory balanceSum = nlpLockedBalanceQueues[subaccount]
                .unlockedBalanceSum;
            balanceSum.amount += amountDelta;
            nlpLockedBalanceQueues[subaccount].unlockedBalanceSum = balanceSum;
        }
    }
```

**File:** core/contracts/SpotEngine.sol (L193-195)
```text
        if (productId == NLP_PRODUCT_ID) {
            handleNlpLockedBalance(subaccount, amountDelta);
        }
```

**File:** core/contracts/SpotEngine.sol (L216-218)
```text
        if (productId == NLP_PRODUCT_ID) {
            handleNlpLockedBalance(subaccount, amountDelta);
        }
```

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
