### Title
Unbounded `while` Loop in `tryUnlockNlpBalance` Can Freeze User NLP Funds — (`SpotEngineState.sol`)

---

### Summary

The `tryUnlockNlpBalance` function in `SpotEngineState.sol` contains an unbounded `while` loop that iterates over every unlocked entry in a per-subaccount `NlpLockedBalanceQueue`. Because the queue grows by one entry for every NLP mint that occurs at a distinct oracle timestamp, a user who mints NLP frequently over time accumulates a large queue. When the lock period expires for all accumulated entries and the user attempts to burn NLP, the loop must process every entry in a single transaction. If the queue is large enough, the transaction reverts due to out-of-gas, permanently blocking the user from burning their NLP.

---

### Finding Description

`SpotEngineState.sol` maintains a per-subaccount `NlpLockedBalanceQueue`:

```solidity
mapping(bytes32 => NlpLockedBalanceQueue) internal nlpLockedBalanceQueues;
``` [1](#0-0) 

The queue is populated inside `handleNlpLockedBalance` in `SpotEngine.sol`. Each positive `amountDelta` (i.e., every NLP mint) appends a new `NlpLockedBalance` entry **unless** the last entry's `unlockedAt` timestamp exactly matches `getOracleTime() + NLP_LOCK_PERIOD`:

```solidity
} else {
    queue.balances[queue.balanceCount] = NlpLockedBalance({
        balance: Balance({amount: amountDelta}),
        unlockedAt: getOracleTime() + NLP_LOCK_PERIOD
    });
    queue.balanceCount++;
}
``` [2](#0-1) 

Because the oracle time advances with every oracle update, mints at different oracle timestamps each create a distinct queue entry. Over time, a user who mints NLP regularly accumulates an unbounded number of entries.

The queue is drained by `tryUnlockNlpBalance`, which loops unconditionally over every entry whose `unlockedAt` has passed:

```solidity
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
``` [3](#0-2) 

This function is called on every NLP balance update (via `handleNlpLockedBalance`) and, critically, from `getNlpUnlockedBalance`:

```solidity
function getNlpUnlockedBalance(bytes32 subaccount)
    external
    returns (Balance memory)
{
    tryUnlockNlpBalance(subaccount);
    ...
}
``` [4](#0-3) 

`getNlpUnlockedBalance` is called directly inside `burnNlp` in `Clearinghouse.sol` to check whether the user holds sufficient unlocked NLP before proceeding with the burn:

```solidity
require(
    spotEngine.getNlpUnlockedBalance(txn.sender).amount >= nlpAmount,
    ERR_UNLOCKED_NLP_INSUFFICIENT
);
``` [5](#0-4) 

If the queue has accumulated N unlocked entries and N is large enough to exceed the block gas limit, `burnNlp` reverts with out-of-gas before any state change occurs. The user's NLP balance remains locked and inaccessible.

---

### Impact Explanation

A user who has minted NLP many times across distinct oracle timestamps accumulates a large `NlpLockedBalanceQueue`. Once the lock period expires for all entries, any attempt to burn NLP triggers `tryUnlockNlpBalance`, which must iterate over every accumulated entry in a single transaction. If the queue length exceeds the gas budget, the burn transaction reverts unconditionally. Because the queue can only be drained by successfully executing `tryUnlockNlpBalance`, and that function itself is what causes the OOG, the user's NLP is permanently frozen — they cannot burn it to recover their underlying quote collateral.

---

### Likelihood Explanation

The condition requires two concurrent factors:
1. A user mints NLP at many distinct oracle timestamps (e.g., once per oracle update over an extended period).
2. The user does not burn NLP during that period, so the queue is never partially drained.

Both conditions are realistic for a long-term NLP holder who mints incrementally and holds without burning. The oracle updates frequently on a high-throughput L2 like Ink Chain, so the queue can grow quickly. No privileged access or external compromise is required — the user's own normal minting behavior is the trigger.

---

### Recommendation

Remove the unbounded `while` loop from `tryUnlockNlpBalance`. Instead, process at most a fixed number of entries per call (a "batch drain" pattern), or restructure the queue so that the unlocked balance sum is maintained incrementally without requiring a full scan. A simpler alternative — analogous to the mitigation suggested in the reference report — is to collapse all pending locked entries into a single running accumulator at mint time, eliminating the need for a per-entry queue entirely.

---

### Proof of Concept

1. User calls `mintNlp` (or any path that invokes `updateBalance` for `NLP_PRODUCT_ID` with a positive delta) once per oracle update for a large number of oracle periods. Each call to `handleNlpLockedBalance` appends a new entry to `nlpLockedBalanceQueues[user]` because `getOracleTime()` advances between calls. [6](#0-5) 

2. After `NLP_LOCK_PERIOD` has elapsed for all entries, the user submits a `burnNlp` transaction (directly via the sequencer or via the slow-mode escape hatch in `Endpoint.sol`). [7](#0-6) 

3. `burnNlp` calls `spotEngine.getNlpUnlockedBalance(txn.sender)`, which calls `tryUnlockNlpBalance`. The `while` loop iterates over all N accumulated entries. For sufficiently large N, the transaction runs out of gas and reverts. [8](#0-7) 

4. Every subsequent burn attempt by the user hits the same loop and reverts. The user's NLP is frozen and cannot be redeemed for quote collateral.

### Citations

**File:** core/contracts/SpotEngineState.sol (L13-13)
```text
    mapping(bytes32 => NlpLockedBalanceQueue) internal nlpLockedBalanceQueues;
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

**File:** core/contracts/SpotEngine.sol (L139-173)
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
