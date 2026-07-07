### Title
Unbounded `tryUnlockNlpBalance` Loop in `burnNlp` Path Causes Permanent OOG Revert, Locking User NLP Tokens — (`core/contracts/SpotEngineState.sol`)

---

### Summary

`SpotEngineState.tryUnlockNlpBalance` contains an unbounded `while` loop that iterates over every matured NLP lock entry for a subaccount. This loop is unconditionally executed during every `burnNlp` operation. A user who has minted NLP at many distinct oracle timestamps accumulates a proportionally large `NlpLockedBalanceQueue`. Once the lock period expires for those entries, the loop must process all of them in a single call with no batching mechanism. If the entry count is large enough, the `burnNlp` transaction always reverts with OOG. Because `BurnNlp` is not supported in the slow-mode path, the sequencer is the only execution route, and a sequencer that drops the OOG transaction leaves the user's NLP permanently unburnable.

---

### Finding Description

`SpotEngineState.tryUnlockNlpBalance` (lines 285–306) iterates over all matured entries in the queue:

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

Each iteration performs at least one `SLOAD` and two `SSTORE` operations (read entry, delete entry, increment pointer), costing roughly 15,000–25,000 gas. With a 30 M gas block limit, approximately 1,200–2,000 iterations exhaust the gas budget.

New queue entries are created in `handleNlpLockedBalance` whenever a `mintNlp` call arrives at a different oracle timestamp than the previous mint:

```solidity
queue.balances[queue.balanceCount] = NlpLockedBalance({
    balance: Balance({amount: amountDelta}),
    unlockedAt: getOracleTime() + NLP_LOCK_PERIOD
});
queue.balanceCount++;
``` [2](#0-1) 

`balanceCount` is monotonically increasing and has no cap. The deduplication check only merges consecutive mints at the *same* oracle second; any mint at a new oracle timestamp creates a fresh entry.

`tryUnlockNlpBalance` is called unconditionally in two places during `burnNlp`:

1. `Clearinghouse.burnNlp` calls `spotEngine.getNlpUnlockedBalance(txn.sender)` (line 499), which calls `tryUnlockNlpBalance`. [3](#0-2) 

2. `spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, -nlpAmount)` (line 511) calls `handleNlpLockedBalance`, which calls `tryUnlockNlpBalance` again. [4](#0-3) [5](#0-4) 

There is no mechanism to process entries in partial batches, no cap on `balanceCount`, and no way to skip the loop. `tryUnlockNlpBalance` is `public` but itself contains the same unbounded loop, so calling it externally as a pre-processing step also OOGs once the entry count is large enough.

`BurnNlp` is handled exclusively in `processTransactionImpl` (sequencer path): [6](#0-5) 

It is absent from `processSlowModeTransactionImpl`, so there is no censorship-resistance fallback. If the sequencer drops the OOG transaction, the user has no alternative execution path.

---

### Impact Explanation

A user whose `NlpLockedBalanceQueue.balanceCount` exceeds the OOG threshold (~1,200–2,000 entries) will find every `burnNlp` transaction reverting. Their NLP tokens represent a proportional claim on the NLP pool's underlying assets. Because burning is the only redemption mechanism and the slow-mode path does not support `BurnNlp`, the tokens become permanently unburnable — a direct, irreversible loss of the user's redeemable value.

---

### Likelihood Explanation

A user following a dollar-cost-averaging strategy who mints NLP once per oracle tick over a period of weeks to months will naturally accumulate hundreds to thousands of queue entries. The oracle time is advanced by the sequencer via `SpotTick` transactions; if ticks arrive every few seconds, the threshold is reachable within days of regular use. No protocol-level warning, cap, or documentation exists to alert users to this risk.

---

### Recommendation

1. **Cap `balanceCount`**: Enforce a maximum number of entries per subaccount (e.g., 200) in `handleNlpLockedBalance`, reverting or merging into the last entry when the cap is reached.
2. **Bounded batch processing**: Modify `tryUnlockNlpBalance` to accept a `maxIterations` parameter so callers can process entries incrementally across multiple transactions before calling `burnNlp`.
3. **Slow-mode support for `BurnNlp`**: Add `BurnNlp` handling to `processSlowModeTransactionImpl` so users retain a censorship-resistant execution path.

---

### Proof of Concept

1. User calls `MintNlp` N times, each at a distinct oracle timestamp (N = 2,000).
2. Each call creates a new `NlpLockedBalance` entry; `queue.balanceCount` reaches 2,000.
3. User waits for `NLP_LOCK_PERIOD` to elapse so all entries satisfy `unlockedAt <= getOracleTime()`.
4. User submits a signed `BurnNlp` transaction to the sequencer.
5. Sequencer calls `submitTransactionsChecked` → `processTransaction` → `processTransactionImpl` → `clearinghouse.burnNlp`.
6. `burnNlp` calls `spotEngine.getNlpUnlockedBalance(txn.sender)` → `tryUnlockNlpBalance`.
7. The `while` loop iterates 2,000 times, each performing SLOAD + SSTORE operations; gas is exhausted before completion.
8. The entire `submitTransactionsChecked` batch reverts (no try/catch at this level).
9. The sequencer drops the transaction; the user's NLP balance remains non-zero but permanently unburnable. [7](#0-6) [8](#0-7) [9](#0-8) [6](#0-5)

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

**File:** core/contracts/SpotEngine.sol (L147-148)
```text
        tryUnlockNlpBalance(subaccount);
        if (amountDelta > 0) {
```

**File:** core/contracts/SpotEngine.sol (L161-166)
```text
            } else {
                queue.balances[queue.balanceCount] = NlpLockedBalance({
                    balance: Balance({amount: amountDelta}),
                    unlockedAt: getOracleTime() + NLP_LOCK_PERIOD
                });
                queue.balanceCount++;
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

**File:** core/contracts/EndpointTx.sol (L554-573)
```text
        } else if (txType == IEndpoint.TransactionType.BurnNlp) {
            IEndpoint.SignedBurnNlp memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedBurnNlp)
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
            clearinghouse.burnNlp(
                signedTx.tx,
                signedTx.oraclePriceX18,
                nlpPools,
                signedTx.nlpPoolRebalanceX18
            );
```
