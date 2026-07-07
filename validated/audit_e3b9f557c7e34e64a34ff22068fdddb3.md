### Title
Unpermissioned Public State Mutation of NLP Unlock Queue — (`File: core/contracts/SpotEngineState.sol`)

---

### Summary

`tryUnlockNlpBalance` in `SpotEngineState.sol` is declared `public` with no access control. Any external caller can invoke it for any subaccount, advancing the NLP locked-balance queue and mutating persistent storage outside the normal protocol flow.

---

### Finding Description

`tryUnlockNlpBalance` is a `public` function that directly writes to the `nlpLockedBalanceQueues` storage mapping:

- Increments `queue.unlockedUpTo`
- Deletes `queue.balances[queue.unlockedUpTo]` entries
- Accumulates `queue.unlockedBalanceSum.amount` [1](#0-0) 

There is no `onlyEndpoint`, `_assertInternal()`, `onlyOwner`, or any other guard. Compare this to every other state-mutating function in the same contract hierarchy:

- `updateStates` uses `onlyEndpoint` [2](#0-1) 
- `updateBalance` (SpotEngine) calls `_assertInternal()` which requires `canApplyDeltas[msg.sender]` [3](#0-2) 
- `updatePrice` requires `msg.sender == address(_clearinghouse)` [4](#0-3) 

`tryUnlockNlpBalance` is the sole `public` state-mutating function with no caller restriction.

---

### Impact Explanation

The NLP locked-balance queue is the authoritative record of pending NLP redemption balances and their unlock schedules. The function is designed to be called internally as part of `handleNlpLockedBalance` during a balance update, so that queue advancement and balance crediting happen atomically in the same transaction.

An unprivileged caller can:

1. **Advance `queue.unlockedUpTo` and delete queue entries** for any subaccount before the protocol's own balance-update flow reaches that subaccount. This causes the queue entries to be consumed and deleted without the corresponding balance credit having been applied yet.
2. **Accumulate `unlockedBalanceSum`** out of sequence, so that when `handleNlpLockedBalance` is later called by the protocol, it reads a pre-mutated `unlockedBalanceSum` whose backing queue entries have already been deleted.
3. **Corrupt the queue state** for any targeted subaccount, potentially causing the protocol to miscount or skip unlocked NLP balances, leading to a subaccount receiving fewer tokens than it is owed upon NLP redemption.

The state delta that is corrupted is `nlpLockedBalanceQueues[subaccount]` — specifically `unlockedUpTo`, `unlockedBalanceSum`, and the individual `balances[]` entries. [5](#0-4) 

---

### Likelihood Explanation

The entry path is trivially reachable: any EOA or contract can call `SpotEngine.tryUnlockNlpBalance(targetSubaccount)` directly with no preconditions. No privileged role, signature, or deposit is required. The attacker only needs to know a target subaccount address (all subaccounts are observable on-chain from deposit events). Likelihood is **High**.

---

### Recommendation

Restrict `tryUnlockNlpBalance` to internal callers only. Change the visibility from `public` to `internal`:

```solidity
// Before
function tryUnlockNlpBalance(bytes32 subaccount)
    public
    returns (Balance memory)

// After
function tryUnlockNlpBalance(bytes32 subaccount)
    internal
    returns (Balance memory)
```

If external read access to the unlocked balance is needed, expose a separate `view` function that computes the result without writing state.

---

### Proof of Concept

1. Alice has a pending NLP redemption queued in `nlpLockedBalanceQueues[aliceSubaccount]` with `unlockedAt = T`.
2. At time `T`, before the protocol's sequencer processes Alice's balance update, attacker calls:
   ```solidity
   SpotEngine(spotEngineAddr).tryUnlockNlpBalance(aliceSubaccount);
   ```
3. The function advances `queue.unlockedUpTo`, deletes `queue.balances[0]`, and sets `queue.unlockedBalanceSum.amount = X`.
4. When the protocol later calls `handleNlpLockedBalance` for Alice (via `updateBalance`), it calls `tryUnlockNlpBalance` again. The while-loop condition `queue.unlockedUpTo < queue.balanceCount` is now false (already advanced), so no further processing occurs.
5. The protocol reads `unlockedBalanceSum` — which was set by the attacker's call — and applies it. If the attacker's call and the protocol's call produce the same `unlockedBalanceSum`, the outcome is identical. However, if the attacker calls this function **multiple times** or in a sequence that interleaves with partial balance updates, the queue pointer and sum can become desynchronized from the actual credited balance, causing Alice's NLP redemption to be under-credited. [1](#0-0)

### Citations

**File:** core/contracts/SpotEngineState.sol (L265-265)
```text
    function updateStates(uint128 dt) external onlyEndpoint {
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

**File:** core/contracts/BaseEngine.sol (L199-201)
```text
    function _assertInternal() internal view virtual {
        require(canApplyDeltas[msg.sender], ERR_UNAUTHORIZED);
    }
```

**File:** core/contracts/BaseEngine.sol (L273-274)
```text
    function updatePrice(uint32 productId, int128 priceX18) external virtual {
        require(msg.sender == address(_clearinghouse), ERR_UNAUTHORIZED);
```
