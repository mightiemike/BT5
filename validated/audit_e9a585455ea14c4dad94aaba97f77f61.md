### Title
Unbounded `while` Loop in `tryUnlockNlpBalance` Enables Permanent NLP Collateral Lock — (`File: core/contracts/SpotEngineState.sol`)

---

### Summary

`tryUnlockNlpBalance` in `SpotEngineState.sol` contains an unbounded `while` loop that iterates over every pending entry in a subaccount's `NlpLockedBalanceQueue`. Because each `mintNlp` call at a distinct oracle timestamp appends a new queue entry with no cap on queue depth, a user who mints NLP across many sequencer batches accumulates an unbounded number of entries. After the 4-day lock period expires, all entries become eligible for processing simultaneously. When `burnNlp` is subsequently called, it invokes `getNlpUnlockedBalance` → `tryUnlockNlpBalance`, which iterates the entire queue in a single transaction. With a sufficiently large queue, the loop exhausts the block gas limit, causing `burnNlp` to revert permanently and locking the user's NLP collateral.

---

### Finding Description

`tryUnlockNlpBalance` iterates a `while` loop with no upper bound on iterations:

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

Each iteration processes one `NlpLockedBalance` entry. Entries are created in `handleNlpLockedBalance`:

```solidity
} else {
    queue.balances[queue.balanceCount] = NlpLockedBalance({
        balance: Balance({amount: amountDelta}),
        unlockedAt: getOracleTime() + NLP_LOCK_PERIOD
    });
    queue.balanceCount++;
}
``` [2](#0-1) 

The deduplication guard only merges entries when the last entry's `unlockedAt` equals `getOracleTime() + NLP_LOCK_PERIOD` exactly: [3](#0-2) 

`getOracleTime()` returns `IEndpoint(endpoint).getTime()` — the sequencer-controlled timestamp that advances with each batch: [4](#0-3) 

`NLP_LOCK_PERIOD` is 4 days: [5](#0-4) 

Because the oracle time advances between sequencer batches, each `mintNlp` submitted in a different batch creates a distinct queue entry. There is no cap on `balanceCount`. Over weeks or months of regular NLP minting, a subaccount accumulates hundreds or thousands of entries.

The critical execution path is in `burnNlp`:

```solidity
require(
    spotEngine.getNlpUnlockedBalance(txn.sender).amount >= nlpAmount,
    ERR_UNLOCKED_NLP_INSUFFICIENT
);
``` [6](#0-5) 

`getNlpUnlockedBalance` unconditionally calls `tryUnlockNlpBalance`: [7](#0-6) 

When the queue is large enough, the `while` loop in `tryUnlockNlpBalance` exhausts the gas limit inside `burnNlp`, causing it to revert on every call. The user's NLP tokens become permanently unburnable.

---

### Impact Explanation

The user's NLP collateral is permanently locked. `burnNlp` is the only mechanism to redeem NLP tokens for quote tokens. If it always reverts due to out-of-gas in `tryUnlockNlpBalance`, the user can never withdraw the underlying quote value of their NLP position. The corrupted state is `nlpLockedBalanceQueues[subaccount].balanceCount` growing without bound, making the `burnNlp` → `getNlpUnlockedBalance` → `tryUnlockNlpBalance` call path permanently non-executable for that subaccount. This is a concrete, irreversible asset loss (locked collateral), not merely a transient DoS.

---

### Likelihood Explanation

Any user who mints NLP regularly across many sequencer batches — a normal behavior for active NLP liquidity providers — naturally accumulates queue entries over time. No adversarial intent is required; the condition arises organically. A user minting NLP once per sequencer batch over several months can accumulate thousands of entries. After the 4-day lock period, all entries become eligible simultaneously, and the first `burnNlp` call triggers the full unbounded iteration. The entry path (`MintNlp` signed transaction → sequencer → `mintNlp` → `updateBalance` → `handleNlpLockedBalance`) is a standard, supported user flow requiring no special privileges.

---

### Recommendation

Cap the number of iterations in `tryUnlockNlpBalance` per call, or enforce a maximum queue depth in `handleNlpLockedBalance`. For example:

1. **Iteration cap**: Process at most `N` entries per call (e.g., 100), and allow callers to invoke `tryUnlockNlpBalance` multiple times to drain the queue incrementally before calling `burnNlp`.
2. **Queue depth cap**: In `handleNlpLockedBalance`, revert or merge aggressively if `queue.balanceCount - queue.unlockedUpTo` exceeds a safe threshold (e.g., 500).
3. **Pre-drain requirement**: Require that `tryUnlockNlpBalance` has been called to drain the queue before `burnNlp` is accepted, and enforce a per-call iteration limit so the drain can always complete within gas bounds.

---

### Proof of Concept

1. User submits `MintNlp` transactions across `K` distinct sequencer batches (each with a different `getOracleTime()` value). Each call to `mintNlp` → `updateBalance(NLP_PRODUCT_ID, sender, nlpAmount)` → `handleNlpLockedBalance` appends a new entry to `nlpLockedBalanceQueues[sender]`, incrementing `balanceCount` to `K`. [8](#0-7) 

2. After 4 days (`NLP_LOCK_PERIOD`), all `K` entries satisfy `unlockedAt <= getOracleTime()`. [5](#0-4) 

3. User submits `BurnNlp`. The sequencer calls `clearinghouse.burnNlp(...)`, which calls `spotEngine.getNlpUnlockedBalance(sender)` → `tryUnlockNlpBalance(sender)`. [9](#0-8) 

4. The `while` loop in `tryUnlockNlpBalance` iterates `K` times, each iteration performing a storage read (`queue.balances[queue.unlockedUpTo].unlockedAt`), a storage write (`queue.unlockedBalanceSum.amount +=`), a `delete`, and an increment. At ~5,000–20,000 gas per iteration, `K ≈ 1,500` entries exhausts a 30M gas block limit. [1](#0-0) 

5. `burnNlp` reverts with out-of-gas on every subsequent call. The user's NLP balance remains non-zero but permanently unburnable, locking the collateral. [10](#0-9)

### Citations

**File:** core/contracts/SpotEngineState.sol (L292-303)
```text
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

**File:** core/contracts/SpotEngine.sol (L152-160)
```text
            if (
                queue.balanceCount > 0 &&
                queue.balances[queue.balanceCount - 1].unlockedAt ==
                getOracleTime() + NLP_LOCK_PERIOD
            ) {
                queue
                    .balances[queue.balanceCount - 1]
                    .balance
                    .amount += amountDelta;
```

**File:** core/contracts/SpotEngine.sol (L161-167)
```text
            } else {
                queue.balances[queue.balanceCount] = NlpLockedBalance({
                    balance: Balance({amount: amountDelta}),
                    unlockedAt: getOracleTime() + NLP_LOCK_PERIOD
                });
                queue.balanceCount++;
            }
```

**File:** core/contracts/EndpointGated.sol (L21-23)
```text
    function getOracleTime() internal view returns (uint128) {
        return IEndpoint(endpoint).getTime();
    }
```

**File:** core/contracts/common/Constants.sol (L52-52)
```text
uint64 constant NLP_LOCK_PERIOD = 4 * 24 * 60 * 60; // 4 days
```

**File:** core/contracts/Clearinghouse.sol (L485-530)
```text
    function burnNlp(
        IEndpoint.BurnNlp calldata txn,
        int128 oraclePriceX18,
        IEndpoint.NlpPool[] calldata nlpPools,
        int128[] calldata nlpPoolRebalanceX18
    ) external onlyEndpoint {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);

        ISpotEngine spotEngine = _spotEngine();
        spotEngine.updatePrice(NLP_PRODUCT_ID, oraclePriceX18);

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

        if (quoteAmount > 0) {
            spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, quoteAmount);
            _applyNlpRebalance(spotEngine, nlpPools, nlpPoolRebalanceX18);
        }

        require(
            spotEngine.getBalance(NLP_PRODUCT_ID, txn.sender).amount >= 0,
            ERR_SUBACCT_HEALTH
        );
        // Burning NLP can decrease health if the burn fee exceeds the health improvement
        // from the withdrawal. This check prevents malicious actors from deliberately
        // creating unhealthy subaccounts through NLP burns.
        require(
            getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,
            ERR_SUBACCT_HEALTH
        );
    }
```
