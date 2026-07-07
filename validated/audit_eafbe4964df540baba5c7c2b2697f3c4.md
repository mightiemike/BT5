### Title
NLP Mint/Burn Front-Running via Stale `oraclePriceX18` Timing — (File: `core/contracts/Clearinghouse.sol`)

---

### Summary

The NLP token system in Nado is structurally analogous to the lsETH share system described in the external report. The `oraclePriceX18` used to compute NLP mint and burn amounts is supplied by the sequencer at execution time and is never validated on-chain against any trusted price bound or stored oracle value. A user who can observe off-chain oracle data can time a `MintNlp` request to be processed just before a pool-value increase, receiving more NLP tokens than the post-update price would allow, and time a `BurnNlp` request just before a pool-value decrease, exiting at the pre-loss price and avoiding the socialized loss.

---

### Finding Description

In `Clearinghouse.mintNlp`, the number of NLP tokens issued is computed as:

```
nlpAmount = quoteAmount / oraclePriceX18
```

In `Clearinghouse.burnNlp`, the quote returned is computed as:

```
quoteAmount = nlpAmount * oraclePriceX18
``` [1](#0-0) [2](#0-1) 

The `oraclePriceX18` is not part of the user-signed `MintNlp` or `BurnNlp` struct. The user only signs `sender`, `quoteAmount`/`nlpAmount`, and `nonce`. The `oraclePriceX18` field lives in the outer `SignedMintNlp`/`SignedBurnNlp` wrapper and is attached by the sequencer at processing time. [3](#0-2) 

In `EndpointTx`, the sequencer writes `oraclePriceX18` directly into the global price store and passes it to the clearinghouse with no on-chain range check, staleness check, or comparison against any stored reference price: [4](#0-3) [5](#0-4) 

The NLP price is therefore updated only when a `MintNlp` or `BurnNlp` transaction is processed. Between those events the stored price is stale. A user who monitors the off-chain oracle feed can observe that the NLP pool's true value has increased (e.g., from accumulated trading fees, funding payments, or favorable PnL) before the sequencer has processed a transaction that would reflect the new price. By submitting a `MintNlp` request at that moment, the user's transaction is processed at the old lower price, yielding more NLP tokens per unit of quote than the true current value warrants. Symmetrically, a user holding unlocked NLP tokens who observes an impending pool loss can submit a `BurnNlp` request before the loss is reflected, exiting at the pre-loss price and transferring the loss to remaining LPs.

A lock period (`NLP_LOCK_PERIOD`) is applied to newly minted NLP balances, which prevents an immediate round-trip mint-then-burn: [6](#0-5) [7](#0-6) 

However, the lock period does not prevent:
1. **Burn front-running** — a user with already-unlocked NLP tokens can exit at any time before a loss is reflected.
2. **Long-horizon mint front-running** — a user can mint at a stale low price, wait for the lock period to expire, and burn at the higher post-update price.

---

### Impact Explanation

- **Mint front-running**: An attacker mints NLP at a stale low price, diluting existing LPs. When the price is updated upward, the attacker's NLP tokens are worth more than the quote they paid, extracting value from the pool.
- **Burn front-running**: An attacker with unlocked NLP burns before a downward price update, avoiding the socialized loss. The loss is borne entirely by remaining LPs.

The corrupted state delta is the `NLP_PRODUCT_ID` balance in `SpotEngine` and the `QUOTE_PRODUCT_ID` balance of the attacker's subaccount. The pool's net asset value per NLP token is understated (mint case) or overstated (burn case) at the moment of the operation. [8](#0-7) [9](#0-8) 

---

### Likelihood Explanation

The NLP oracle price is observable off-chain. Any user who monitors the off-chain price feed and the sequencer's transaction queue can identify windows where the on-chain price lags the true pool value. The attack requires no privileged access — only a signed `MintNlp` or `BurnNlp` transaction submitted at the right moment. The sequencer processes transactions in submission order; if the user's request arrives before the price-updating event, it is processed at the stale price. The burn variant is immediately exploitable by any holder of unlocked NLP tokens.

---

### Recommendation

1. **Bound `oraclePriceX18` on-chain**: Require that the `oraclePriceX18` supplied in `MintNlp`/`BurnNlp` does not deviate from the last stored NLP price by more than a configurable percentage (e.g., ±5%). This prevents large stale-price exploits even if the sequencer lags.
2. **Commit-reveal or TWAP**: Replace the spot `oraclePriceX18` with a time-weighted average price computed over a recent window, making short-term timing attacks unprofitable.
3. **Extend the lock period to cover expected oracle update intervals**: Ensure `NLP_LOCK_PERIOD` is long enough that a mint-then-burn round-trip cannot capture a single oracle update cycle.
4. **Off-chain monitoring**: Monitor the sequencer's NLP price update cadence and alert on large deviations between the on-chain NLP price and the true pool NAV.

---

### Proof of Concept

1. Attacker observes off-chain that the NLP pool NAV has increased from 1.00 to 1.05 USDC/NLP due to accumulated trading fees, but the on-chain `priceX18[NLP_PRODUCT_ID]` still reads 1.00 (no `MintNlp`/`BurnNlp` has been processed since the fees accrued).
2. Attacker submits a `SignedMintNlp` with `quoteAmount = 100_000e18`. The sequencer processes it at `oraclePriceX18 = 1.00e18`, minting `100_000` NLP tokens. [10](#0-9) 
3. The sequencer subsequently processes a transaction that updates the NLP price to 1.05.
4. After `NLP_LOCK_PERIOD` elapses, attacker's NLP balance becomes unlocked. [11](#0-10) 
5. Attacker submits a `SignedBurnNlp` for `100_000` NLP at `oraclePriceX18 = 1.05e18`, receiving `105_000e18` quote (minus the 0.1% burn fee ≈ `104_895e18`). [2](#0-1) 
6. Net profit ≈ 4,895 USDC extracted from existing LPs who earned the fees.

For the burn variant: a holder of unlocked NLP observes an impending pool loss, submits `BurnNlp` at the current price before the loss is reflected, and exits at full value while remaining LPs absorb the loss.

### Citations

**File:** core/contracts/Clearinghouse.sol (L464-466)
```text
        require(txn.quoteAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        int128 quoteAmount = int128(txn.quoteAmount);
        int128 nlpAmount = quoteAmount.div(oraclePriceX18);
```

**File:** core/contracts/Clearinghouse.sol (L473-477)
```text
        spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, nlpAmount);
        spotEngine.updateBalance(NLP_PRODUCT_ID, N_ACCOUNT, -nlpAmount);

        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -quoteAmount);
        _applyNlpRebalance(spotEngine, nlpPools, nlpPoolRebalanceX18);
```

**File:** core/contracts/Clearinghouse.sol (L502-504)
```text
        int128 quoteAmount = nlpAmount.mul(oraclePriceX18);
        int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);
        quoteAmount = MathHelper.max(0, quoteAmount - burnFee);
```

**File:** core/contracts/Clearinghouse.sol (L511-516)
```text
        spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, -nlpAmount);
        spotEngine.updateBalance(NLP_PRODUCT_ID, N_ACCOUNT, nlpAmount);

        if (quoteAmount > 0) {
            spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, quoteAmount);
            _applyNlpRebalance(spotEngine, nlpPools, nlpPoolRebalanceX18);
```

**File:** core/contracts/interfaces/IEndpoint.sol (L113-136)
```text
        bytes32 sender;
        uint128 quoteAmount;
        uint64 nonce;
    }

    struct SignedMintNlp {
        MintNlp tx;
        bytes signature;
        int128 oraclePriceX18;
        int128[] nlpPoolRebalanceX18;
    }

    struct BurnNlp {
        bytes32 sender;
        uint128 nlpAmount;
        uint64 nonce;
    }

    struct SignedBurnNlp {
        BurnNlp tx;
        bytes signature;
        int128 oraclePriceX18;
        int128[] nlpPoolRebalanceX18;
    }
```

**File:** core/contracts/EndpointTx.sol (L547-553)
```text
            priceX18[NLP_PRODUCT_ID] = signedTx.oraclePriceX18;
            clearinghouse.mintNlp(
                signedTx.tx,
                signedTx.oraclePriceX18,
                nlpPools,
                signedTx.nlpPoolRebalanceX18
            );
```

**File:** core/contracts/EndpointTx.sol (L567-573)
```text
            priceX18[NLP_PRODUCT_ID] = signedTx.oraclePriceX18;
            clearinghouse.burnNlp(
                signedTx.tx,
                signedTx.oraclePriceX18,
                nlpPools,
                signedTx.nlpPoolRebalanceX18
            );
```

**File:** core/contracts/SpotEngine.sol (L129-136)
```text
    function getNlpUnlockedBalance(bytes32 subaccount)
        external
        returns (Balance memory)
    {
        tryUnlockNlpBalance(subaccount);
        Balance memory balanceSum = nlpLockedBalanceQueues[subaccount]
            .unlockedBalanceSum;
        return balanceSum;
```

**File:** core/contracts/SpotEngine.sol (L148-167)
```text
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
