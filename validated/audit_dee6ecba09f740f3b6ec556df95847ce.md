### Title
Unbounded `while` Loop in `tryUnlockNlpBalance` Can Permanently Lock User NLP Tokens - (`File: core/contracts/SpotEngineState.sol`)

---

### Summary

`tryUnlockNlpBalance` in `SpotEngineState.sol` iterates over every entry in a per-subaccount `NlpLockedBalanceQueue` with no iteration cap. A user who mints NLP many times at distinct oracle timestamps accumulates an unbounded number of queue entries. Once the queue is large enough, every call to `burnNlp` (which invokes `tryUnlockNlpBalance` internally) runs out of gas, permanently locking the user's NLP tokens in the contract.

---

### Finding Description

`tryUnlockNlpBalance` contains an unbounded `while` loop:

```solidity
while (
    queue.unlockedUpTo < queue.balanceCount &&
    queue.balances[queue.unlockedUpTo].unlockedAt <= getOracleTime()
) {
    queue.unlockedBalanceSum.amount += queue.balances[queue.unlockedUpTo].balance.amount;
    delete queue.balances[queue.unlockedUpTo];
    queue.unlockedUpTo++;
}
``` [1](#0-0) 

The queue is grown in `handleNlpLockedBalance` inside `SpotEngine.sol`. Each `mintNlp` call at a distinct oracle timestamp appends a **new** `NlpLockedBalance` entry:

```solidity
queue.balances[queue.balanceCount] = NlpLockedBalance({
    balance: Balance({amount: amountDelta}),
    unlockedAt: getOracleTime() + NLP_LOCK_PERIOD
});
queue.balanceCount++;
``` [2](#0-1) 

The queue backing store is `mapping(uint64 => NlpLockedBalance)`, which has no capacity bound: [3](#0-2) 

`tryUnlockNlpBalance` is called from two places in `SpotEngine.sol`:

1. `getNlpUnlockedBalance` (line 133) — called by `Clearinghouse.burnNlp` at line 499.
2. `handleNlpLockedBalance` (line 147) — called on every NLP balance update, including every subsequent `mintNlp`. [4](#0-3) [5](#0-4) 

The critical burn path is:

`Clearinghouse.burnNlp` → `spotEngine.getNlpUnlockedBalance(txn.sender)` → `tryUnlockNlpBalance` (unbounded loop) [6](#0-5) 

---

### Impact Explanation

Once a subaccount's `NlpLockedBalanceQueue` grows beyond the gas limit threshold, every call to `burnNlp` for that subaccount reverts with out-of-gas. The user's NLP tokens are permanently unburnable and therefore permanently locked in the protocol — they cannot be redeemed for quote collateral. The corrupted state is `nlpLockedBalanceQueues[subaccount].balanceCount` growing without bound, making the loop cost O(N) storage reads and deletes per call. [7](#0-6) 

---

### Likelihood Explanation

Any user who submits `MintNlp` transactions at many distinct oracle timestamps accumulates one queue entry per mint. Because the sequencer processes transactions and advances oracle time continuously, a user who mints NLP repeatedly over time (e.g., dollar-cost averaging into the NLP pool) will naturally grow their queue. There is no protocol-level limit on how many times a user may mint NLP. The attack is self-inflicted but the outcome — permanent token lock — is a high-severity asset loss for the affected user. [8](#0-7) 

---

### Recommendation

Introduce a per-call iteration cap (analogous to the `batchingLimit` fix described in the external report). Process at most `N` entries per invocation of `tryUnlockNlpBalance` and allow callers to invoke it multiple times (pagination). For example:

```solidity
function tryUnlockNlpBalance(bytes32 subaccount, uint64 maxIterations)
    public
    returns (Balance memory)
{
    NlpLockedBalanceQueue storage queue = nlpLockedBalanceQueues[subaccount];
    uint64 limit = queue.unlockedUpTo + maxIterations;
    while (
        queue.unlockedUpTo < queue.balanceCount &&
        queue.unlockedUpTo < limit &&
        queue.balances[queue.unlockedUpTo].unlockedAt <= getOracleTime()
    ) {
        queue.unlockedBalanceSum.amount += queue.balances[queue.unlockedUpTo].balance.amount;
        delete queue.balances[queue.unlockedUpTo];
        queue.unlockedUpTo++;
    }
    return queue.unlockedBalanceSum;
}
```

Internal callers (e.g., `handleNlpLockedBalance`) should pass a conservative fixed cap; `burnNlp` should require that the queue is fully drained before proceeding, or accept a caller-supplied pagination parameter.

---

### Proof of Concept

1. User calls `MintNlp` (via signed transaction through the sequencer) `K` times, each at a different oracle timestamp. Each call reaches `Clearinghouse.mintNlp` → `SpotEngine.updateBalance(NLP_PRODUCT_ID, sender, +amount)` → `handleNlpLockedBalance` → appends entry `K` to `nlpLockedBalanceQueues[sender]`. [9](#0-8) 

2. After the `NLP_LOCK_PERIOD` elapses, the user submits a `BurnNlp` transaction. The sequencer processes it: `Clearinghouse.burnNlp` → `spotEngine.getNlpUnlockedBalance(sender)` → `tryUnlockNlpBalance`. [10](#0-9) 

3. `tryUnlockNlpBalance` iterates over all `K` entries. For sufficiently large `K` (empirically a few thousand SSTORE/SLOAD operations exhaust the 30M block gas limit), the transaction reverts with out-of-gas. [1](#0-0) 

4. Every subsequent `BurnNlp` attempt also reverts. The user's NLP balance is permanently unburnable — their collateral is locked in the protocol with no recovery path. [4](#0-3)

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

**File:** core/contracts/SpotEngine.sol (L129-137)
```text
    function getNlpUnlockedBalance(bytes32 subaccount)
        external
        returns (Balance memory)
    {
        tryUnlockNlpBalance(subaccount);
        Balance memory balanceSum = nlpLockedBalanceQueues[subaccount]
            .unlockedBalanceSum;
        return balanceSum;
    }
```

**File:** core/contracts/SpotEngine.sol (L147-167)
```text
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
```

**File:** core/contracts/interfaces/engine/ISpotEngine.sol (L53-58)
```text
    struct NlpLockedBalanceQueue {
        mapping(uint64 => NlpLockedBalance) balances;
        uint64 balanceCount;
        uint64 unlockedUpTo;
        Balance unlockedBalanceSum;
    }
```

**File:** core/contracts/Clearinghouse.sol (L473-474)
```text
        spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, nlpAmount);
        spotEngine.updateBalance(NLP_PRODUCT_ID, N_ACCOUNT, -nlpAmount);
```

**File:** core/contracts/Clearinghouse.sol (L498-501)
```text
        require(
            spotEngine.getNlpUnlockedBalance(txn.sender).amount >= nlpAmount,
            ERR_UNLOCKED_NLP_INSUFFICIENT
        );
```
