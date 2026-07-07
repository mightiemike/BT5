### Title
Unbounded `while` Loop in `tryUnlockNlpBalance` Enables DoS on NLP Burns and Health Checks — (`SpotEngineState.sol`)

---

### Summary

The `tryUnlockNlpBalance` function in `SpotEngineState.sol` contains an unbounded `while` loop that iterates over every unlocked entry in a user's `NlpLockedBalanceQueue`. Because new queue entries are appended each time NLP is minted at a distinct oracle timestamp, an attacker who can trigger NLP credits to a victim's subaccount at many different oracle times can inflate the victim's queue to the point where the loop exhausts the block gas limit. This permanently blocks the victim from burning NLP (withdrawing funds) and from passing health checks, causing a loss of funds.

---

### Finding Description

**Root cause — unbounded loop:**

`SpotEngineState.sol` lines 285–306 define `tryUnlockNlpBalance`:

```solidity
function tryUnlockNlpBalance(bytes32 subaccount)
    public
    returns (Balance memory)
{
    NlpLockedBalanceQueue storage queue = nlpLockedBalanceQueues[subaccount];
    while (
        queue.unlockedUpTo < queue.balanceCount &&
        queue.balances[queue.unlockedUpTo].unlockedAt <= getOracleTime()
    ) {
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

The loop has no upper bound; it processes every entry from `unlockedUpTo` to `balanceCount` whose `unlockedAt` timestamp has passed. [1](#0-0) 

**Queue growth mechanism:**

`SpotEngine.sol` `handleNlpLockedBalance` (lines 139–173) appends a **new** `NlpLockedBalance` entry to the queue whenever NLP is credited to a subaccount at an oracle time that differs from the last entry's `unlockedAt`:

```solidity
} else {
    queue.balances[queue.balanceCount] = NlpLockedBalance({
        balance: Balance({amount: amountDelta}),
        unlockedAt: getOracleTime() + NLP_LOCK_PERIOD
    });
    queue.balanceCount++;
}
```

Each NLP credit at a distinct oracle timestamp creates one new queue slot. [2](#0-1) 

**Attacker-controlled entry path:**

The `NlpLockedBalanceQueue` is keyed by `subaccount` (bytes32), not by `msg.sender`. Any protocol path that calls `SpotEngine.updateBalance(NLP_PRODUCT_ID, victimSubaccount, positiveAmount)` will invoke `handleNlpLockedBalance` for the victim. Architecturally, NLP transfers between subaccounts (analogous to `TransferQuote`) route through `updateBalance` with `productId == NLP_PRODUCT_ID`. An attacker who holds any NLP can submit signed transfer transactions to the sequencer, crediting dust amounts to the victim's subaccount at successive oracle timestamps (one per block). Each transfer at a new oracle time appends a fresh entry to the victim's queue. [3](#0-2) 

**Trigger points:**

`tryUnlockNlpBalance` is called in two critical paths:

1. `getNlpUnlockedBalance` (SpotEngine.sol line 133) — called from Clearinghouse during health evaluation. [4](#0-3) 
2. `handleNlpLockedBalance` (SpotEngine.sol line 147) — called on every NLP balance update, including NLP burns. [5](#0-4) 

Once the queue is large enough, both paths run out of gas.

---

### Impact Explanation

- **NLP burns permanently blocked**: Any transaction that reduces a victim's NLP balance calls `handleNlpLockedBalance` → `tryUnlockNlpBalance`. With a sufficiently large queue, this reverts with OOG, preventing the victim from ever burning NLP or withdrawing the underlying collateral. This is a direct, permanent loss of funds.
- **Health checks blocked**: `getNlpUnlockedBalance` is called from Clearinghouse during health evaluation. If the victim's health check OOGs, liquidations and any collateral operations on that subaccount are also blocked, compounding the impact.

---

### Likelihood Explanation

The attack requires the attacker to:
1. Hold a minimal amount of NLP (dust amounts suffice).
2. Submit one NLP transfer per oracle tick to the victim's subaccount over many blocks.

The cost is low (dust NLP per transfer plus sequencer fees). The `NlpLockedBalanceQueue` mapping uses `uint64` indices, so there is no hard cap on `balanceCount`. The attack is fully permissionless and requires no privileged access.

---

### Recommendation

Replace the unbounded `while` loop in `tryUnlockNlpBalance` with a bounded iteration (process at most N entries per call), or restructure the queue so that the unlock step is O(1). Specifically:

- **Bounded processing**: Accept a `maxEntries` parameter and process at most that many entries per call, returning a flag indicating whether more remain.
- **Lazy accumulation**: Instead of iterating at unlock time, compute the unlocked sum lazily using the queue's monotonic `unlockedUpTo` pointer with a single-step advance per call.
- **Merge on credit**: The existing merge logic (lines 152–160 of `SpotEngine.sol`) already collapses same-timestamp credits into one entry. Extend this to also merge entries whose `unlockedAt` is within a minimum granularity window, reducing queue growth rate. [6](#0-5) 

---

### Proof of Concept

```
1. Attacker acquires 1 wei of NLP.
2. For i = 1 to N (e.g., N = 10,000):
     a. Attacker signs a transfer of 1 wei NLP to victimSubaccount.
     b. Sequencer processes the transfer in block i (distinct oracle time).
     c. handleNlpLockedBalance appends entry i to victim's NlpLockedBalanceQueue.
3. After NLP_LOCK_PERIOD elapses, all N entries become unlocked.
4. Victim submits a burn-NLP transaction.
5. handleNlpLockedBalance → tryUnlockNlpBalance iterates N entries → OOG.
6. Victim's NLP is permanently locked; burn reverts every time.
```

The `NlpLockedBalanceQueue` struct confirms the unbounded mapping storage: [7](#0-6)

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

**File:** core/contracts/interfaces/engine/ISpotEngine.sol (L53-58)
```text
    struct NlpLockedBalanceQueue {
        mapping(uint64 => NlpLockedBalance) balances;
        uint64 balanceCount;
        uint64 unlockedUpTo;
        Balance unlockedBalanceSum;
    }
```
