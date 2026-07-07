### Title
Unbounded `NlpLockedBalanceQueue` Growth Causes Permanent NLP Fund Lock via Gas Exhaustion in `tryUnlockNlpBalance` - (File: core/contracts/SpotEngineState.sol)

---

### Summary

`SpotEngine.handleNlpLockedBalance` appends a new entry to a per-subaccount `NlpLockedBalanceQueue` on every NLP mint that occurs at a distinct oracle timestamp, with no cap on the queue length. `SpotEngineState.tryUnlockNlpBalance` then iterates over every unlocked entry in a single unbounded `while` loop. A user who mints NLP across many distinct oracle timestamps will eventually cause `tryUnlockNlpBalance` to exhaust the block gas limit, permanently bricking all NLP operations (`burnNlp`, subsequent `mintNlp`) for that subaccount and locking their NLP tokens in the contract.

---

### Finding Description

`SpotEngine.handleNlpLockedBalance` is called from `SpotEngine.updateBalance` whenever `productId == NLP_PRODUCT_ID`:

```solidity
// SpotEngine.sol lines 148-173
if (
    queue.balanceCount > 0 &&
    queue.balances[queue.balanceCount - 1].unlockedAt ==
    getOracleTime() + NLP_LOCK_PERIOD
) {
    queue.balances[queue.balanceCount - 1].balance.amount += amountDelta;
} else {
    queue.balances[queue.balanceCount] = NlpLockedBalance({
        balance: Balance({amount: amountDelta}),
        unlockedAt: getOracleTime() + NLP_LOCK_PERIOD
    });
    queue.balanceCount++;   // ← no cap
}
``` [1](#0-0) 

Each mint at a different oracle timestamp creates a new queue slot and increments `queue.balanceCount` without bound.

`SpotEngineState.tryUnlockNlpBalance` then processes all matured entries in one unbounded `while` loop:

```solidity
// SpotEngineState.sol lines 292-303
while (
    queue.unlockedUpTo < queue.balanceCount &&
    queue.balances[queue.unlockedUpTo].unlockedAt <= getOracleTime()
) {
    queue.unlockedBalanceSum.amount += queue.balances[queue.unlockedUpTo].balance.amount;
    delete queue.balances[queue.unlockedUpTo];
    queue.unlockedUpTo++;
}
``` [2](#0-1) 

`tryUnlockNlpBalance` is called unconditionally from two paths that are exercised on every NLP operation:

1. `SpotEngine.getNlpUnlockedBalance` → called by `Clearinghouse.burnNlp` to check the unlocked balance before burning. [3](#0-2) 

2. `SpotEngine.handleNlpLockedBalance` → called from `SpotEngine.updateBalance(NLP_PRODUCT_ID, ...)` → triggered by both `Clearinghouse.mintNlp` and `Clearinghouse.burnNlp`. [4](#0-3) 

`Clearinghouse.burnNlp` calls both paths: [5](#0-4) 

Once `queue.balanceCount` is large enough that iterating over all matured entries in a single transaction exceeds the block gas limit, every call to `burnNlp` or `mintNlp` for that subaccount reverts permanently.

---

### Impact Explanation

The user's NLP tokens become permanently unburnable. `burnNlp` is the only mechanism to redeem NLP for quote collateral. Because `tryUnlockNlpBalance` is called unconditionally inside both `getNlpUnlockedBalance` and `handleNlpLockedBalance`, and both are invoked on every NLP operation, there is no code path that allows the user to burn or mint NLP without triggering the unbounded loop. The NLP balance is effectively frozen in the contract with no recovery path, constituting a direct, irreversible loss of user funds.

---

### Likelihood Explanation

A user who participates in NLP minting over an extended period — minting small amounts regularly at different oracle timestamps — will naturally accumulate queue entries. The sequencer advances the oracle time with each batch, so any user who mints NLP across N distinct oracle time values accumulates N queue entries. This is normal protocol usage, not an adversarial edge case. The threshold at which gas exhaustion occurs depends on the block gas limit of Ink Chain, but given that each loop iteration performs a storage read, a storage delete, and an addition, the limit can be reached with a few thousand entries, which is achievable over weeks of regular use.

---

### Recommendation

Cap `queue.balanceCount` at a protocol-defined maximum (e.g., 100 or 256). When the cap is reached, merge the new mint amount into the most recent existing entry rather than creating a new slot. Alternatively, process only a bounded number of entries per call in `tryUnlockNlpBalance` (e.g., up to 50 per invocation) and track progress across calls, so that no single transaction is required to drain the entire queue.

---

### Proof of Concept

1. User submits 2000 `mintNlp` transactions to the sequencer, each processed at a distinct oracle timestamp (e.g., one per sequencer batch over several days).
2. Each call to `Clearinghouse.mintNlp` → `spotEngine.updateBalance(NLP_PRODUCT_ID, sender, amount)` → `handleNlpLockedBalance` → `queue.balanceCount++`. After 2000 mints, `queue.balanceCount == 2000`.
3. After `NLP_LOCK_PERIOD` elapses, all 2000 entries are matured (`unlockedAt <= getOracleTime()`).
4. User submits a `burnNlp` transaction. The sequencer processes it: `Clearinghouse.burnNlp` → `spotEngine.getNlpUnlockedBalance(sender)` → `tryUnlockNlpBalance` → the `while` loop iterates 2000 times, each performing a storage read and delete. The transaction reverts with out-of-gas.
5. Every subsequent `burnNlp` or `mintNlp` call for this subaccount also reverts. The user's NLP balance is permanently locked. [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

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
