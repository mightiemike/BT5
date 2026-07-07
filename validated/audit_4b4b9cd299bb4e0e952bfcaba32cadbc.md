### Title
NLP Lock Period Bypass via Unchecked `unlockedBalanceSum` Underflow in `handleNlpLockedBalance` — (File: `core/contracts/SpotEngine.sol`)

---

### Summary

The `handleNlpLockedBalance` function in `SpotEngine.sol` decrements `unlockedBalanceSum` when NLP is burned without checking whether the result goes negative. Because the health check used to gate NLP burns operates on the total NLP balance (locked + unlocked combined), a user whose entire NLP position is still within the 4-day lock period can burn locked NLP immediately, bypassing the lock entirely. This is a direct analog to the BondManager invariant failure: just as the bond manager had no on-chain state distinguishing available from locked collateral, the NLP engine has no on-chain enforcement that only unlocked NLP can be redeemed.

---

### Finding Description

`SpotEngine.handleNlpLockedBalance` is called from `SpotEngine.updateBalance` whenever the NLP product balance changes. When `amountDelta > 0` (minting), the amount is pushed into a time-locked queue with `unlockedAt = getOracleTime() + NLP_LOCK_PERIOD` (4 days). When `amountDelta < 0` (burning), the code does:

```solidity
} else if (amountDelta < 0) {
    Balance memory balanceSum = nlpLockedBalanceQueues[subaccount]
        .unlockedBalanceSum;
    balanceSum.amount += amountDelta;          // amountDelta is negative
    nlpLockedBalanceQueues[subaccount].unlockedBalanceSum = balanceSum;
}
``` [1](#0-0) 

There is no `require(balanceSum.amount + amountDelta >= 0)` guard. `tryUnlockNlpBalance` is called first to move any newly matured entries into `unlockedBalanceSum`, but if the user has zero unlocked NLP (all still locked), `unlockedBalanceSum.amount` is driven negative. The actual NLP balance in `balances[NLP_PRODUCT_ID][subaccount]` is then reduced by `_updateBalanceNormalized` without any separate locked-vs-unlocked gate:

```solidity
if (productId == NLP_PRODUCT_ID) {
    handleNlpLockedBalance(subaccount, amountDelta);
}
_updateBalanceNormalized(state, balance, amountDelta);
``` [2](#0-1) 

The downstream `withdrawCollateral` path enforces only `getHealth(sender, INITIAL) >= 0` and `assertUtilization`, both of which operate on the aggregate NLP balance, not on the unlocked subset. [3](#0-2) 

The `NlpLockedBalanceQueue` struct stores `unlockedBalanceSum` as a signed `Balance` with no floor, so the underflow is silently accepted:

```solidity
struct NlpLockedBalanceQueue {
    mapping(uint64 => NlpLockedBalance) balances;
    uint64 balanceCount;
    uint64 unlockedUpTo;
    Balance unlockedBalanceSum;   // can go negative — no invariant enforced
}
``` [4](#0-3) 

The lock period constant is 4 days:

```solidity
uint64 constant NLP_LOCK_PERIOD = 4 * 24 * 60 * 60; // 4 days
``` [5](#0-4) 

---

### Impact Explanation

A user who has just minted NLP (entire balance locked, `unlockedBalanceSum = 0`) can immediately burn that NLP. `handleNlpLockedBalance` drives `unlockedBalanceSum` to a negative value without reverting. The actual NLP balance is reduced, and the user receives quote back. The 4-day lock is bypassed entirely. Additionally, the negative `unlockedBalanceSum` persists in storage; when future lock entries mature and are added to `unlockedBalanceSum` via `tryUnlockNlpBalance`, they partially cancel the negative debt rather than being credited to the user, corrupting accounting for all subsequent unlock operations on that subaccount. [6](#0-5) 

---

### Likelihood Explanation

Any NLP holder can trigger this immediately after minting, with no special privileges. The entry path is a standard user-facing NLP burn/redemption transaction routed through the offchain exchange or clearinghouse. No admin access, sequencer compromise, or governance capture is required. The only prerequisite is holding NLP tokens, which is a normal user action. Likelihood is **high** for any user who wants to exit an NLP position before the lock period expires.

---

### Recommendation

1. In `handleNlpLockedBalance`, when `amountDelta < 0`, add an explicit check:
   ```solidity
   require(
       balanceSum.amount + amountDelta >= 0,
       "ERR_NLP_LOCKED"
   );
   ```
   This enforces the invariant `unlockedBalanceSum >= 0` and prevents burning locked NLP. [1](#0-0) 

2. Analogous to the BondManager recommendation, distinguish between available (unlocked) and locked NLP in health and withdrawal checks so that locked NLP does not count toward redeemable collateral until the lock period has elapsed. [7](#0-6) 

---

### Proof of Concept

1. User calls the NLP mint path → `updateBalance(NLP_PRODUCT_ID, subaccount, +100, quoteDelta)` is executed. `handleNlpLockedBalance` pushes 100 into the locked queue with `unlockedAt = now + 4 days`. `unlockedBalanceSum.amount = 0`. [8](#0-7) 

2. Immediately (same block), user calls the NLP burn/redemption path → `updateBalance(NLP_PRODUCT_ID, subaccount, -100, quoteDelta)` is executed.

3. `handleNlpLockedBalance` is entered with `amountDelta = -100`. `tryUnlockNlpBalance` runs but unlocks nothing (lock not expired). `unlockedBalanceSum.amount += -100` → `unlockedBalanceSum.amount = -100`. No revert. [1](#0-0) 

4. `_updateBalanceNormalized` reduces the actual NLP balance by 100. Health check passes (user's quote balance is restored). User has successfully redeemed locked NLP 4 days early. [9](#0-8) 

5. `unlockedBalanceSum.amount = -100` persists. When the original lock entry matures 4 days later and `tryUnlockNlpBalance` runs, it adds 100 to the already-negative sum, netting to 0 — the user's future unlock credit is silently consumed, permanently corrupting the queue accounting for that subaccount. [10](#0-9)

### Citations

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

**File:** core/contracts/SpotEngine.sol (L168-173)
```text
        } else if (amountDelta < 0) {
            Balance memory balanceSum = nlpLockedBalanceQueues[subaccount]
                .unlockedBalanceSum;
            balanceSum.amount += amountDelta;
            nlpLockedBalanceQueues[subaccount].unlockedBalanceSum = balanceSum;
        }
```

**File:** core/contracts/SpotEngine.sol (L193-198)
```text
        if (productId == NLP_PRODUCT_ID) {
            handleNlpLockedBalance(subaccount, amountDelta);
        }

        _updateBalanceNormalized(state, balance, amountDelta);
        _updateBalanceNormalized(quoteState, quoteBalance, quoteDelta);
```

**File:** core/contracts/Clearinghouse.sol (L415-419)
```text
        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
```

**File:** core/contracts/interfaces/engine/ISpotEngine.sol (L53-58)
```text
    struct NlpLockedBalanceQueue {
        mapping(uint64 => NlpLockedBalance) balances;
        uint64 balanceCount;
        uint64 unlockedUpTo;
        Balance unlockedBalanceSum;
    }
```

**File:** core/contracts/common/Constants.sol (L52-52)
```text
uint64 constant NLP_LOCK_PERIOD = 4 * 24 * 60 * 60; // 4 days
```

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
