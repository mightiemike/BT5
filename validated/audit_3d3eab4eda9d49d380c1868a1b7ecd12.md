### Title
Stale `unlockedBalanceSum` in `NlpLockedBalanceQueue` Allows Burning Locked NLP Before Unlock Period Expires — (File: `core/contracts/SpotEngineState.sol`)

---

### Summary

The `unlockedBalanceSum` field inside `NlpLockedBalanceQueue` is only ever incremented when locked NLP entries mature, but is never decremented when NLP is burned. `Clearinghouse.burnNlp()` gates burns on this stale cumulative sum, allowing any user who has previously unlocked and burned NLP to immediately burn newly-minted (still-locked) NLP, bypassing the lock period entirely.

---

### Finding Description

**Root cause — `unlockedBalanceSum` is never decremented on burn**

`SpotEngineState.tryUnlockNlpBalance()` accumulates matured locked-balance entries into `queue.unlockedBalanceSum`:

```solidity
queue.unlockedBalanceSum.amount += queue.balances[queue.unlockedUpTo].balance.amount;
delete queue.balances[queue.unlockedUpTo];
queue.unlockedUpTo++;
``` [1](#0-0) 

This running total is returned as the "unlocked balance" and is the sole gate in `Clearinghouse.burnNlp()`:

```solidity
require(
    spotEngine.getNlpUnlockedBalance(txn.sender).amount >= nlpAmount,
    ERR_UNLOCKED_NLP_INSUFFICIENT
);
``` [2](#0-1) 

When NLP is actually burned, only the normalized spot balance is decremented:

```solidity
spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, -nlpAmount);
``` [3](#0-2) 

`_updateBalanceNormalized` updates `balances[NLP_PRODUCT_ID][subaccount].amountNormalized` but never touches `nlpLockedBalanceQueues[subaccount].unlockedBalanceSum`. [4](#0-3) 

The result: `unlockedBalanceSum` is a stale cached value after any burn. It retains the pre-burn total, making the unlock-gate check meaningless for subsequent burns.

**Secondary safety check does not close the gap**

After the burn, `burnNlp` checks:

```solidity
require(
    spotEngine.getBalance(NLP_PRODUCT_ID, txn.sender).amount >= 0,
    ERR_SUBACCT_HEALTH
);
``` [5](#0-4) 

This only prevents the actual NLP balance from going negative. It does not enforce that the burned NLP was unlocked. A user whose actual balance is exactly equal to their locked (not-yet-matured) NLP will pass this check while burning locked tokens.

---

### Impact Explanation

**Impact: High**

Any user can bypass the NLP lock period and immediately redeem locked NLP for quote tokens. This undermines the economic security model of the NLP system: the lock period exists to prevent users from minting NLP, extracting yield or arbitrage, and immediately exiting. With this bug, a user can:

1. Mint NLP, wait for the lock to expire, burn all of it (establishing a stale `unlockedBalanceSum`).
2. Mint new NLP (locked).
3. Immediately burn the new locked NLP using the stale sum as proof of unlock eligibility.
4. Receive quote tokens without waiting for the lock period.

The corrupted state is `nlpLockedBalanceQueues[subaccount].unlockedBalanceSum` — a persistent on-chain value that diverges from the user's true currently-unlocked NLP balance.

---

### Likelihood Explanation

**Likelihood: Medium**

The precondition is that the attacker has previously minted NLP, waited for it to unlock, and burned it — a normal user flow. After that one-time setup, every subsequent mint-and-immediately-burn cycle is exploitable. No privileged access, sequencer cooperation, or external dependency is required. The entry path is the standard `BurnNlp` signed transaction submitted through the sequencer's normal processing pipeline. [6](#0-5) 

---

### Recommendation

Decrement `unlockedBalanceSum` by the burned amount inside `SpotEngine.updateBalance` (or a dedicated NLP burn hook) whenever `productId == NLP_PRODUCT_ID` and the delta is negative. Specifically, after reducing the normalized balance, reduce `nlpLockedBalanceQueues[subaccount].unlockedBalanceSum.amount` by `min(burnAmount, unlockedBalanceSum.amount)` to keep the cached value consistent with the actual unlocked-and-not-yet-burned NLP.

---

### Proof of Concept

1. Alice mints 100 NLP (locked for 30 days). `unlockedBalanceSum[Alice] = 0`.
2. 30 days pass. `tryUnlockNlpBalance(Alice)` is called → `unlockedBalanceSum[Alice] = 100`.
3. Alice calls `burnNlp(100)`. Actual NLP balance → 0. `unlockedBalanceSum[Alice]` remains **100** (not decremented).
4. Alice mints 50 new NLP (locked for 30 days, unlock time = now + 30 days). Actual NLP balance → 50.
5. Alice immediately calls `burnNlp(50)`.
   - `getNlpUnlockedBalance(Alice)` calls `tryUnlockNlpBalance` → no new entries mature → returns stale `unlockedBalanceSum = 100`.
   - `100 >= 50` ✓ — unlock gate passes.
   - `spotEngine.updateBalance(NLP_PRODUCT_ID, Alice, -50)` → actual balance = 0.
   - `getBalance(NLP_PRODUCT_ID, Alice).amount = 0 >= 0` ✓ — health check passes.
6. Alice receives quote tokens for 50 NLP **29 days before the lock expires**.

### Citations

**File:** core/contracts/SpotEngineState.sol (L15-50)
```text
    function _updateBalanceNormalized(
        State memory state,
        BalanceNormalized memory balance,
        int128 balanceDelta
    ) internal pure {
        if (balance.amountNormalized > 0) {
            state.totalDepositsNormalized -= balance.amountNormalized;
        } else {
            state.totalBorrowsNormalized += balance.amountNormalized;
        }

        int128 cumulativeMultiplierX18;
        if (balance.amountNormalized > 0) {
            cumulativeMultiplierX18 = state.cumulativeDepositsMultiplierX18;
        } else {
            cumulativeMultiplierX18 = state.cumulativeBorrowsMultiplierX18;
        }

        int128 newAmount = balance.amountNormalized.mul(
            cumulativeMultiplierX18
        ) + balanceDelta;

        if (newAmount > 0) {
            cumulativeMultiplierX18 = state.cumulativeDepositsMultiplierX18;
        } else {
            cumulativeMultiplierX18 = state.cumulativeBorrowsMultiplierX18;
        }

        balance.amountNormalized = newAmount.div(cumulativeMultiplierX18);

        if (balance.amountNormalized > 0) {
            state.totalDepositsNormalized += balance.amountNormalized;
        } else {
            state.totalBorrowsNormalized -= balance.amountNormalized;
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

**File:** core/contracts/Clearinghouse.sol (L498-501)
```text
        require(
            spotEngine.getNlpUnlockedBalance(txn.sender).amount >= nlpAmount,
            ERR_UNLOCKED_NLP_INSUFFICIENT
        );
```

**File:** core/contracts/Clearinghouse.sol (L511-512)
```text
        spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, -nlpAmount);
        spotEngine.updateBalance(NLP_PRODUCT_ID, N_ACCOUNT, nlpAmount);
```

**File:** core/contracts/Clearinghouse.sol (L519-522)
```text
        require(
            spotEngine.getBalance(NLP_PRODUCT_ID, txn.sender).amount >= 0,
            ERR_SUBACCT_HEALTH
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
