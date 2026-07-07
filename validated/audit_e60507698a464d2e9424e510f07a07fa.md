### Title
`getNlpUnlockedBalance` Performs Hidden State Mutation via `tryUnlockNlpBalance` Side-Effect — (`File: core/contracts/SpotEngine.sol`)

---

### Summary

`getNlpUnlockedBalance` in `SpotEngine.sol` is named and exposed as a getter, but it unconditionally calls `tryUnlockNlpBalance`, a state-mutating function that modifies `nlpLockedBalanceQueues` storage. This is a direct analog of the UMA M02 class: a function whose name implies a read-only query but silently performs a state transition.

---

### Finding Description

`getNlpUnlockedBalance` is declared `external` (not `view`) and calls `tryUnlockNlpBalance(subaccount)` before returning the balance: [1](#0-0) 

```solidity
function getNlpUnlockedBalance(bytes32 subaccount)
    external
    returns (Balance memory)
{
    tryUnlockNlpBalance(subaccount);          // <-- state mutation
    Balance memory balanceSum = nlpLockedBalanceQueues[subaccount]
        .unlockedBalanceSum;
    return balanceSum;
}
```

`tryUnlockNlpBalance` processes the `nlpLockedBalanceQueues[subaccount]` queue and mutates `unlockedBalanceSum` — the same storage field that `handleNlpLockedBalance` writes to when NLP is minted or burned. [2](#0-1) 

The function is called inside `burnNlp` in `Clearinghouse.sol` as a guard check: [3](#0-2) 

```solidity
require(
    spotEngine.getNlpUnlockedBalance(txn.sender).amount >= nlpAmount,
    ERR_UNLOCKED_NLP_INSUFFICIENT
);
```

This means the **eligibility check itself** triggers the unlock state transition. The two actions — "read how much is unlocked" and "advance the unlock queue" — are fused into a single call with a misleading name.

---

### Impact Explanation

Two concrete impacts:

1. **Unprivileged external trigger of queue state mutation**: Because `getNlpUnlockedBalance` is `external` with no access control, any unprivileged caller can invoke it on any `subaccount` at any time. This forces `tryUnlockNlpBalance` to run and mutate `nlpLockedBalanceQueues[subaccount].unlockedBalanceSum` for an arbitrary subaccount outside of the intended `burnNlp` / `handleNlpLockedBalance` flow. If the unlock queue processing has any ordering dependency or if the `unlockedBalanceSum` is expected to be advanced only during a burn, this external trigger corrupts the queue state for that subaccount.

2. **Developer-induced double-mutation**: Any future developer reading the name `getNlpUnlockedBalance` will treat it as a pure query and may call it multiple times (e.g., for a pre-check, a simulation, or a UI read path). Each call advances the unlock queue, causing `unlockedBalanceSum` to be mutated more times than intended, potentially desynchronizing the locked/unlocked accounting.

The corrupted state is `nlpLockedBalanceQueues[subaccount].unlockedBalanceSum`, which directly controls how much NLP a user is permitted to burn via the `burnNlp` guard. [4](#0-3) 

---

### Likelihood Explanation

**Medium.** The external callability is unconditional — any address can call `getNlpUnlockedBalance` on any subaccount with no restriction. The trigger path requires no special privilege, no governance action, and no leaked key. The misleading name makes accidental double-invocation by developers highly likely during future maintenance.

---

### Recommendation

Split the function into two:

- A pure `view` getter that reads `nlpLockedBalanceQueues[subaccount].unlockedBalanceSum` without advancing the queue.
- A separate mutating function (e.g., `unlockNlpBalance`) that explicitly advances the queue, called only from `burnNlp` and `handleNlpLockedBalance`.

Alternatively, rename `getNlpUnlockedBalance` to `unlockAndGetNlpBalance` to make the side-effect explicit, and add an access control modifier restricting it to internal protocol callers.

---

### Proof of Concept

1. Alice has NLP locked in `nlpLockedBalanceQueues[aliceSubaccount]` with `unlockedAt` in the past (eligible to unlock).
2. Attacker (any address) calls `SpotEngine.getNlpUnlockedBalance(aliceSubaccount)` directly — no permission required.
3. `tryUnlockNlpBalance(aliceSubaccount)` runs, advancing the queue and writing to `unlockedBalanceSum`.
4. When `burnNlp` is later called for Alice, `getNlpUnlockedBalance` is called again inside the guard check, running `tryUnlockNlpBalance` a second time on an already-processed queue.
5. Depending on `tryUnlockNlpBalance`'s idempotency guarantees, this double-processing can corrupt `unlockedBalanceSum`, causing the burn guard to pass or fail incorrectly. [1](#0-0) [3](#0-2)

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
