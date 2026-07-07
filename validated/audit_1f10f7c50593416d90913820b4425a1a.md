### Title
NLP Minters Cannot Exit Locked Positions During 4-Day Lock Period, Exposing Them to Unavoidable Price Risk — (File: `core/contracts/SpotEngine.sol`, `core/contracts/Clearinghouse.sol`)

---

### Summary

When a user mints NLP tokens, the entire minted balance is placed into a time-locked queue for `NLP_LOCK_PERIOD = 4 days`. During this window, `burnNlp` unconditionally reverts for that user because the on-chain unlocked-balance check returns zero. There is no early-exit path — not even a penalised one — leaving the user fully exposed to NLP price movements for four days with no recourse.

---

### Finding Description

**Step 1 — Mint locks the balance.**

`Clearinghouse.mintNlp()` calls `spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, nlpAmount)`. [1](#0-0) 

That call routes into `handleNlpLockedBalance` in `SpotEngine.sol`, which enqueues the minted amount with `unlockedAt = getOracleTime() + NLP_LOCK_PERIOD`. [2](#0-1) 

`NLP_LOCK_PERIOD` is hardcoded to **4 days** (345 600 seconds). [3](#0-2) 

**Step 2 — Burn is gated on the unlocked balance.**

`Clearinghouse.burnNlp()` calls `spotEngine.getNlpUnlockedBalance(txn.sender)` and requires the result to be `>= nlpAmount`. While the lock is active, `getNlpUnlockedBalance` returns `unlockedBalanceSum`, which is zero for a freshly minted position. [4](#0-3) 

`getNlpUnlockedBalance` itself calls `tryUnlockNlpBalance`, which only moves entries from the queue into `unlockedBalanceSum` once `unlockedAt <= getOracleTime()`. [5](#0-4) 

**Step 3 — No alternative exit path exists.**

- The slow-mode queue (`submitSlowModeTransaction`) does not contain a `BurnNlp` variant that bypasses the lock; any sequencer-processed `BurnNlp` still executes `Clearinghouse.burnNlp()` with the same on-chain check. [6](#0-5) 
- `N_ACCOUNT` is explicitly exempted from the lock, but ordinary user subaccounts are not. [7](#0-6) 
- There is no penalty-based early-exit function anywhere in the contract set.

---

### Impact Explanation

A user who mints NLP is irrevocably committed to the position for four days. During that window:

- The NLP oracle price (`oraclePriceX18`) is set by the sequencer at burn time, not at mint time; a large adverse move in pool PnL (e.g., sustained trader profits drawn from the NLP pools) directly reduces the quote amount the user receives on eventual burn.
- Other users can freely mint and burn (once their own lock expires), changing pool composition and effective NLP price while the locked user cannot react.
- The user's quote collateral has already been debited at mint time (`spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -quoteAmount)`), so the funds are fully at risk with zero ability to cut losses. [8](#0-7) 

**Concrete asset delta:** A user who mints NLP worth $X of quote tokens and experiences a 20 % NLP price decline during the lock period loses $0.2X with no on-chain mechanism to prevent it.

---

### Likelihood Explanation

- Any user who mints NLP is automatically affected; no special precondition is required.
- Four days is a long window in a leveraged derivatives protocol; significant NLP price moves are realistic given that the NLP pool is the direct counterparty to all perpetual and spot traders.
- The trigger is the ordinary `MintNlp` user flow, reachable by any unprivileged caller via `Endpoint.submitSlowModeTransaction` or the sequencer path.

---

### Recommendation

1. **Penalised early exit:** Allow users to burn locked NLP at a higher fee (e.g., 1–5 % of notional) before the lock expires, crediting the penalty to the insurance fund or NLP pool.
2. **Partial unlock on price deviation:** Introduce an on-chain oracle-price-deviation threshold; if NLP price moves beyond X % from the price at mint time, allow the locked holder to exit at the current price minus a fixed penalty.
3. **Shorter or tiered lock:** Reduce `NLP_LOCK_PERIOD` or make it proportional to the minted amount, limiting the maximum exposure window.

---

### Proof of Concept

```
1. Alice calls depositCollateral() to deposit 10 000 USDC.
2. Sequencer processes MintNlp for Alice at oraclePriceX18 = 1.00.
   → Alice receives 10 000 NLP, locked until block.timestamp + 4 days.
   → Alice's USDC balance is debited 10 000.

3. Over the next 4 days, traders profit heavily against the NLP pool.
   NLP oracle price falls to 0.70.

4. Alice submits BurnNlp for 10 000 NLP.
   → Clearinghouse.burnNlp() calls getNlpUnlockedBalance(Alice).
   → unlockedBalanceSum.amount == 0 (lock not yet expired).
   → Reverts with ERR_UNLOCKED_NLP_INSUFFICIENT.

5. Alice is forced to wait until the lock expires.
   On day 4+, she burns at 0.70 → receives 7 000 USDC (minus burn fee).
   Loss: ~3 000 USDC with no on-chain mechanism to have avoided it.
```

### Citations

**File:** core/contracts/Clearinghouse.sol (L473-474)
```text
        spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, nlpAmount);
        spotEngine.updateBalance(NLP_PRODUCT_ID, N_ACCOUNT, -nlpAmount);
```

**File:** core/contracts/Clearinghouse.sol (L476-477)
```text
        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -quoteAmount);
        _applyNlpRebalance(spotEngine, nlpPools, nlpPoolRebalanceX18);
```

**File:** core/contracts/Clearinghouse.sol (L498-501)
```text
        require(
            spotEngine.getNlpUnlockedBalance(txn.sender).amount >= nlpAmount,
            ERR_UNLOCKED_NLP_INSUFFICIENT
        );
```

**File:** core/contracts/SpotEngine.sol (L144-145)
```text
        // N_ACCOUNT is not limited by lock period
        if (subaccount == N_ACCOUNT) return;
```

**File:** core/contracts/SpotEngine.sol (L162-165)
```text
                queue.balances[queue.balanceCount] = NlpLockedBalance({
                    balance: Balance({amount: amountDelta}),
                    unlockedAt: getOracleTime() + NLP_LOCK_PERIOD
                });
```

**File:** core/contracts/SpotEngine.sol (L292-303)
```text
    }
}
```

**File:** core/contracts/common/Constants.sol (L52-52)
```text
uint64 constant NLP_LOCK_PERIOD = 4 * 24 * 60 * 60; // 4 days
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
