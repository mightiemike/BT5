### Title
Unbounded `while` Loop in `tryUnlockNlpBalance` Locks User NLP Tokens via Out-of-Gas - (File: `core/contracts/SpotEngineState.sol`)

---

### Summary

`tryUnlockNlpBalance` in `SpotEngineState.sol` iterates over every entry in a per-subaccount `NlpLockedBalanceQueue` with no upper bound. A user who mints NLP across many distinct oracle ticks accumulates unboundedly many queue entries. Once the queue is large enough, every subsequent NLP operation (`MintNlp`, `BurnNlp`) triggers the same unbounded loop and reverts with out-of-gas, permanently locking the user's NLP tokens with no chunked-claim escape hatch.

---

### Finding Description

`tryUnlockNlpBalance` iterates over `nlpLockedBalanceQueues[subaccount]` in a single `while` loop:

```solidity
// SpotEngineState.sol lines 292–303
while (
    queue.unlockedUpTo < queue.balanceCount &&
    queue.balances[queue.unlockedUpTo].unlockedAt <= getOracleTime()
) {
    queue.unlockedBalanceSum.amount += queue.balances[queue.unlockedUpTo].balance.amount;
    delete queue.balances[queue.unlockedUpTo];
    queue.unlockedUpTo++;
}
``` [1](#0-0) 

The queue grows inside `handleNlpLockedBalance` in `SpotEngine.sol`. A new entry is appended whenever `amountDelta > 0` **and** the last entry's `unlockedAt` timestamp differs from `getOracleTime() + NLP_LOCK_PERIOD`:

```solidity
// SpotEngine.sol lines 161–167
queue.balances[queue.balanceCount] = NlpLockedBalance({
    balance: Balance({amount: amountDelta}),
    unlockedAt: getOracleTime() + NLP_LOCK_PERIOD
});
queue.balanceCount++;
``` [2](#0-1) 

`handleNlpLockedBalance` is called unconditionally from both `updateBalance` overloads whenever `productId == NLP_PRODUCT_ID`: [3](#0-2) [4](#0-3) 

The oracle time advances with every `SpotTick` sequencer transaction. A user who mints NLP across N distinct oracle ticks accumulates N separate queue entries. After `NLP_LOCK_PERIOD` elapses, all N entries become eligible for processing in a single `while` loop iteration. There is no parameter to limit how many entries are processed per call, and no alternative code path that bypasses the loop.

---

### Impact Explanation

Once the queue depth is large enough to exhaust the block gas limit inside `tryUnlockNlpBalance`, every call to `updateBalance(NLP_PRODUCT_ID, subaccount, ...)` reverts. This blocks:

- **`BurnNlp`** — the user's only mechanism to redeem NLP tokens for underlying collateral.
- **`MintNlp`** — any further NLP minting by the same subaccount.

The user's NLP balance is permanently frozen. There is no chunked-unlock path: `tryUnlockNlpBalance` is always invoked in full, and the `BurnNlp` flow has no way to skip or partially drain the queue before attempting the burn.

**Corrupted state:** `nlpLockedBalanceQueues[subaccount].unlockedBalanceSum` is never updated, so the user's redeemable NLP balance is permanently understated and the tokens are irrecoverable.

---

### Likelihood Explanation

Any NLP liquidity provider who mints NLP regularly over time — across different oracle ticks — will accumulate queue entries. The oracle time advances with every `SpotTick` transaction, which is a routine sequencer operation. A user making one small NLP mint per oracle tick will grow their queue at the same rate as oracle ticks are processed. This is a realistic usage pattern for active NLP participants, not a contrived edge case.

The user-signed `MintNlp` transaction is the direct entry point: [5](#0-4) 

No special privilege is required — any subaccount holder can trigger this path.

---

### Recommendation

Add a `maxEntries` parameter to `tryUnlockNlpBalance` so callers can process the queue in bounded chunks:

```solidity
function tryUnlockNlpBalance(bytes32 subaccount, uint64 maxEntries)
    public
    returns (Balance memory)
{
    NlpLockedBalanceQueue storage queue = nlpLockedBalanceQueues[subaccount];
    uint64 processed = 0;
    while (
        queue.unlockedUpTo < queue.balanceCount &&
        queue.balances[queue.unlockedUpTo].unlockedAt <= getOracleTime() &&
        processed < maxEntries
    ) {
        queue.unlockedBalanceSum.amount += queue.balances[queue.unlockedUpTo].balance.amount;
        delete queue.balances[queue.unlockedUpTo];
        queue.unlockedUpTo++;
        processed++;
    }
    return queue.unlockedBalanceSum;
}
```

Internal callers (`handleNlpLockedBalance`) should pass a safe bounded value (e.g., 50). Users should be able to call `tryUnlockNlpBalance` directly with a small `maxEntries` to drain their queue incrementally before attempting a `BurnNlp`.

---

### Proof of Concept

1. Alice mints a small amount of NLP via `MintNlp` on each of N distinct oracle ticks (each `SpotTick` advances `getOracleTime()`).
2. After each mint, `handleNlpLockedBalance` appends a new entry to `nlpLockedBalanceQueues[Alice]` because the timestamp differs from the previous entry. `queue.balanceCount` reaches N.
3. After `NLP_LOCK_PERIOD` elapses, all N entries satisfy `unlockedAt <= getOracleTime()`.
4. Alice submits a `BurnNlp` transaction. The sequencer calls `clearinghouse.burnNlp(...)` → `spotEngine.updateBalance(NLP_PRODUCT_ID, Alice, -amount)` → `handleNlpLockedBalance(Alice, -amount)` → `tryUnlockNlpBalance(Alice)`.
5. The `while` loop iterates N times. For sufficiently large N (e.g., N ≈ 5,000–10,000 depending on gas costs per iteration), the transaction reverts with out-of-gas.
6. Every subsequent `BurnNlp` or `MintNlp` attempt by Alice hits the same loop and reverts. Alice's NLP is permanently locked. [1](#0-0) [6](#0-5) [7](#0-6)

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
