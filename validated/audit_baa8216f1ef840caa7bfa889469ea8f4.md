### Title
Unbounded `NlpLockedBalanceQueue` Growth Causes Permanent NLP Fund Lock - (`File: core/contracts/SpotEngineState.sol`, `core/contracts/SpotEngine.sol`)

---

### Summary

`SpotEngine.handleNlpLockedBalance()` appends a new entry to `nlpLockedBalanceQueues[subaccount]` on every NLP mint at a distinct oracle timestamp, with no cap on `balanceCount`. `SpotEngineState.tryUnlockNlpBalance()` iterates the entire queue in an unbounded `while` loop and is called on every subsequent NLP balance update. A user who mints NLP across enough distinct oracle timestamps can grow their queue until any future `MintNlp` or `BurnNlp` transaction for their subaccount exceeds the block gas limit, permanently freezing their NLP position.

---

### Finding Description

`NlpLockedBalanceQueue` is defined in `ISpotEngine` as a mapping-backed queue with a `uint64 balanceCount` counter and no maximum size: [1](#0-0) 

In `SpotEngine.handleNlpLockedBalance()`, every call with `amountDelta > 0` where the current oracle time differs from the last entry's `unlockedAt - NLP_LOCK_PERIOD` creates a new queue slot and increments `balanceCount`: [2](#0-1) 

There is no guard limiting how large `balanceCount` can grow. `NLP_LOCK_PERIOD` is 4 days, meaning every deposit at a distinct oracle second produces a distinct entry: [3](#0-2) 

`tryUnlockNlpBalance()` in `SpotEngineState` iterates the queue from `unlockedUpTo` to `balanceCount` in an unbounded `while` loop: [4](#0-3) 

This function is called unconditionally at the top of every `handleNlpLockedBalance()` invocation: [5](#0-4) 

`handleNlpLockedBalance()` is itself called inside both `updateBalance` overloads whenever `productId == NLP_PRODUCT_ID`: [6](#0-5) [7](#0-6) 

---

### Impact Explanation

Once a subaccount's `balanceCount` grows large enough that iterating the queue in `tryUnlockNlpBalance()` exceeds the block gas limit, every subsequent `MintNlp` and `BurnNlp` transaction for that subaccount reverts. The user's locked NLP balance becomes permanently inaccessible — they can neither mint additional NLP nor burn existing NLP to recover their deposited collateral. The corrupted state is `nlpLockedBalanceQueues[subaccount].balanceCount` growing without bound, with the concrete asset impact being the permanent freeze of the subaccount's NLP position and the underlying collateral it represents.

---

### Likelihood Explanation

The entry path is the `MintNlp` transaction type, processed in `EndpointTx.processTransactionImpl()`: [8](#0-7) 

This is a standard user-facing operation requiring only a valid signed transaction and payment of `HEALTHCHECK_FEE` ($1). A user who mints NLP many times across different oracle timestamps — a normal usage pattern for a liquidity provider — will naturally accumulate queue entries. The merging optimization (line 152–160 of `SpotEngine.sol`) only consolidates deposits at the exact same oracle second, so any two mints separated by even one second produce distinct entries. A user making daily NLP deposits over months would accumulate hundreds of entries; a user making frequent small deposits could reach a gas-exhausting count much faster. [9](#0-8) 

---

### Recommendation

1. **Cap `balanceCount`**: Enforce a maximum queue size (e.g., 100 entries) in `handleNlpLockedBalance()`. If the cap is reached, merge the new deposit into the most recent existing entry rather than creating a new slot.
2. **Bound the unlock loop**: In `tryUnlockNlpBalance()`, process at most `N` entries per call (analogous to the post-audit fix described in the reference report), and allow callers to invoke it multiple times to drain the queue incrementally.
3. **Minimum mint size**: Enforce a minimum NLP mint amount per transaction to raise the cost of accumulating many entries.

---

### Proof of Concept

1. User calls `MintNlp` (via `Endpoint.submitTransactions`) at oracle time `T`.  
   → `handleNlpLockedBalance(subaccount, +X)` → `queue.balanceCount` becomes 1, entry `unlockedAt = T + 4 days`.

2. User calls `MintNlp` at oracle time `T+1`.  
   → `tryUnlockNlpBalance` runs (nothing to unlock yet), then a new entry is appended → `balanceCount` becomes 2.

3. Repeat N times at distinct oracle seconds.  
   → `balanceCount` = N, all entries have `unlockedAt` in the future.

4. After 4 days, user calls `BurnNlp`.  
   → `handleNlpLockedBalance` calls `tryUnlockNlpBalance`, which must iterate all N entries in the `while` loop.  
   → For sufficiently large N (empirically ~a few thousand SSTORE/SLOAD operations), the transaction reverts with out-of-gas.  
   → All subsequent `MintNlp` and `BurnNlp` calls for this subaccount also revert.  
   → The subaccount's NLP balance is permanently frozen. [10](#0-9) [11](#0-10)

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

**File:** core/contracts/common/Constants.sol (L52-52)
```text
uint64 constant NLP_LOCK_PERIOD = 4 * 24 * 60 * 60; // 4 days
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

**File:** core/contracts/EndpointTx.sol (L534-553)
```text
        } else if (txType == IEndpoint.TransactionType.MintNlp) {
            IEndpoint.SignedMintNlp memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedMintNlp)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            chargeFee(signedTx.tx.sender, HEALTHCHECK_FEE);
            priceX18[NLP_PRODUCT_ID] = signedTx.oraclePriceX18;
            clearinghouse.mintNlp(
                signedTx.tx,
                signedTx.oraclePriceX18,
                nlpPools,
                signedTx.nlpPoolRebalanceX18
            );
```
