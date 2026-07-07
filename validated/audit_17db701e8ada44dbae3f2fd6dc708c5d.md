### Title
Unbounded NLP Locked Balance Queue Growth Causes Permanent OOG DoS on NLP Operations — (`File: core/contracts/SpotEngine.sol`)

---

### Summary

The `handleNlpLockedBalance` function in `SpotEngine` appends a new entry to a per-subaccount queue (`nlpLockedBalanceQueues`) every time NLP is minted at a distinct oracle timestamp, with no cap on `balanceCount`. The function `tryUnlockNlpBalance` — which iterates over this queue — is called on every NLP balance update. A user who mints NLP repeatedly across many distinct oracle timestamps accumulates an unbounded queue. Once all lock periods expire, a single subsequent NLP operation must process the entire queue in one call, potentially exceeding the block gas limit and permanently locking the user's NLP balance.

---

### Finding Description

In `SpotEngine.handleNlpLockedBalance`, when `amountDelta > 0` and the last queue entry's `unlockedAt` does not match the current oracle time plus `NLP_LOCK_PERIOD`, a new `NlpLockedBalance` entry is unconditionally appended:

```solidity
queue.balances[queue.balanceCount] = NlpLockedBalance({
    balance: Balance({amount: amountDelta}),
    unlockedAt: getOracleTime() + NLP_LOCK_PERIOD
});
queue.balanceCount++;
```

There is no upper bound check on `queue.balanceCount` before this increment. [1](#0-0) 

`tryUnlockNlpBalance(subaccount)` is called at the top of `handleNlpLockedBalance` on every NLP balance update, and also from `getNlpUnlockedBalance`. [2](#0-1) 

`getNlpUnlockedBalance` is called inside `burnNlp` in `Clearinghouse` to check whether the user has sufficient unlocked NLP before allowing a burn: [3](#0-2) 

The `mintNlp` path in `Clearinghouse` is reachable by any user via the sequencer-submitted `MintNlp` transaction type. Each mint at a new oracle timestamp grows the queue by one entry. [4](#0-3) 

Because `tryUnlockNlpBalance` must scan the queue to find and accumulate unlocked entries, a queue with N entries that have all just passed their lock period requires O(N) work in a single call. If N is large enough to exceed the block gas limit, every subsequent NLP operation for that subaccount — including `burnNlp` — will revert with OOG, permanently trapping the user's NLP balance.

---

### Impact Explanation

A user whose `nlpLockedBalanceQueues` queue has grown beyond the gas-processable threshold cannot:
- Burn NLP to recover quote tokens (calls `getNlpUnlockedBalance` → `tryUnlockNlpBalance`)
- Mint additional NLP (calls `handleNlpLockedBalance` → `tryUnlockNlpBalance`)
- Receive any NLP balance update

The user's NLP tokens are permanently frozen. Since NLP represents a claim on the protocol's liquidity pool, this constitutes a direct, irreversible loss of funds for the affected subaccount.

---

### Likelihood Explanation

Any user interacting with the NLP system can trigger this condition by minting NLP at many distinct oracle timestamps. The sequencer batches transactions, so each batch submission that includes a `MintNlp` for the same subaccount at a new oracle time grows the queue by one. A user who mints NLP across hundreds of sequencer submissions — a normal usage pattern for a liquidity provider — will accumulate a large queue. The condition is reachable without any privileged access and without any adversarial intent; it can occur organically for active NLP participants.

---

### Recommendation

Enforce a hard cap on `queue.balanceCount` before appending a new entry in `handleNlpLockedBalance`. For example:

```solidity
require(queue.balanceCount < MAX_NLP_LOCK_QUEUE_DEPTH, "NLP lock queue full");
```

Alternatively, redesign the queue to merge entries at the same lock-period bucket (already partially done for same-timestamp entries) and enforce a maximum number of distinct lock buckets per subaccount. The cap value should be chosen so that iterating over the full queue fits comfortably within the block gas limit.

---

### Proof of Concept

1. User calls `mintNlp` via the sequencer at oracle time T₁ → queue entry 1 added (`unlockedAt = T₁ + NLP_LOCK_PERIOD`). [5](#0-4) 
2. User repeats at oracle times T₂, T₃, … T_N (each a distinct timestamp) → queue grows to N entries, all with `unlockedAt` in the future. [6](#0-5) 
3. After `NLP_LOCK_PERIOD` elapses, all N entries are unlocked.
4. User calls `burnNlp`. `Clearinghouse.burnNlp` calls `spotEngine.getNlpUnlockedBalance(txn.sender)`. [3](#0-2) 
5. `getNlpUnlockedBalance` calls `tryUnlockNlpBalance(subaccount)`, which must iterate over all N entries. [7](#0-6) 
6. For sufficiently large N, the call reverts with OOG. Every subsequent `burnNlp` attempt also reverts. The user's NLP is permanently locked.

### Citations

**File:** core/contracts/SpotEngine.sol (L133-136)
```text
        tryUnlockNlpBalance(subaccount);
        Balance memory balanceSum = nlpLockedBalanceQueues[subaccount]
            .unlockedBalanceSum;
        return balanceSum;
```

**File:** core/contracts/SpotEngine.sol (L147-148)
```text
        tryUnlockNlpBalance(subaccount);
        if (amountDelta > 0) {
```

**File:** core/contracts/SpotEngine.sol (L153-156)
```text
                queue.balanceCount > 0 &&
                queue.balances[queue.balanceCount - 1].unlockedAt ==
                getOracleTime() + NLP_LOCK_PERIOD
            ) {
```

**File:** core/contracts/SpotEngine.sol (L161-164)
```text
            } else {
                queue.balances[queue.balanceCount] = NlpLockedBalance({
                    balance: Balance({amount: amountDelta}),
                    unlockedAt: getOracleTime() + NLP_LOCK_PERIOD
```

**File:** core/contracts/Clearinghouse.sol (L453-483)
```text
    function mintNlp(
        IEndpoint.MintNlp calldata txn,
        int128 oraclePriceX18,
        IEndpoint.NlpPool[] calldata nlpPools,
        int128[] calldata nlpPoolRebalanceX18
    ) external onlyEndpoint {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);

        ISpotEngine spotEngine = _spotEngine();
        spotEngine.updatePrice(NLP_PRODUCT_ID, oraclePriceX18);

        require(txn.quoteAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        int128 quoteAmount = int128(txn.quoteAmount);
        int128 nlpAmount = quoteAmount.div(oraclePriceX18);

        _validateNlpRebalance(nlpPools, nlpPoolRebalanceX18, quoteAmount);
        for (uint128 i = 0; i < nlpPoolRebalanceX18.length; i++) {
            require(nlpPoolRebalanceX18[i] >= 0, ERR_INVALID_NLP_REBALANCE);
        }

        spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, nlpAmount);
        spotEngine.updateBalance(NLP_PRODUCT_ID, N_ACCOUNT, -nlpAmount);

        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -quoteAmount);
        _applyNlpRebalance(spotEngine, nlpPools, nlpPoolRebalanceX18);

        require(
            getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0,
            ERR_SUBACCT_HEALTH
        );
    }
```

**File:** core/contracts/Clearinghouse.sol (L498-501)
```text
        require(
            spotEngine.getNlpUnlockedBalance(txn.sender).amount >= nlpAmount,
            ERR_UNLOCKED_NLP_INSUFFICIENT
        );
```
