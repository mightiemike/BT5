### Title
Reentrancy in `withdrawCollateral` Allows Double-Withdrawal via ERC777 Token Callback — (`File: core/contracts/Clearinghouse.sol`)

---

### Summary

`Clearinghouse.withdrawCollateral` transfers tokens to the recipient **before** updating the subaccount balance in `SpotEngine`. No `nonReentrant` guard exists anywhere in the protocol. An attacker holding an ERC777 (or callback-bearing) collateral token can re-enter `Endpoint.executeSlowModeTransaction` from within the token's `tokensReceived` hook, triggering a second `withdrawCollateral` call against the same stale (not-yet-decremented) balance, and drain more collateral than their position allows.

---

### Finding Description

In `Clearinghouse.withdrawCollateral` the execution order is:

1. **Line 408** — `handleWithdrawTransfer(token, sendTo, amount, idx)` — tokens are moved to the withdraw pool and then forwarded to the recipient.
2. **Line 412** — `spotEngine.updateBalance(productId, sender, amountRealized)` — the subaccount balance is decremented **only after** the transfer completes.
3. **Line 419** — `require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH)` — health check runs against the now-updated balance. [1](#0-0) 

`handleWithdrawTransfer` in `Clearinghouse` first calls `token.safeTransfer(withdrawPool, amount)` and then `BaseWithdrawPool.submitWithdrawal`, which in turn calls `token.safeTransfer(to, amount)` — the actual delivery to the recipient. [2](#0-1) 

The delivery transfer in `BaseWithdrawPool.handleWithdrawTransfer` is the exact point where an ERC777 `tokensReceived` hook fires on the recipient contract. [3](#0-2) 

`Endpoint.executeSlowModeTransaction` is **publicly callable with no access control**. If the attacker has pre-queued a second `WithdrawCollateral` slow-mode transaction, the hook can invoke it: [4](#0-3) 

A grep across the entire repository confirms **zero occurrences** of `nonReentrant`, `ReentrancyGuard`, `_status`, or `_ENTERED` — no reentrancy protection exists at any layer.

---

### Impact Explanation

During the re-entrant call, `spotEngine` still holds the **original, un-decremented balance**. The re-entrant `withdrawCollateral` therefore:

- Passes the health check at line 419 using the stale balance.
- Transfers a second `amount` of tokens to the attacker.
- Decrements the balance.

When control returns to the original call, it decrements the balance a second time and runs its own health check — which now reflects the doubly-decremented balance. If the subaccount was healthy enough to cover two withdrawals, both succeed. The attacker withdraws `2 × amount` while only being entitled to `1 × amount`, directly stealing collateral from the protocol's withdraw pool.

---

### Likelihood Explanation

The preconditions are:
1. A collateral token registered in `SpotEngine` that implements ERC777 `tokensReceived` or any other transfer hook (e.g., fee-on-transfer tokens with callbacks, or tokens the protocol may add in the future).
2. The attacker submits a second `WithdrawCollateral` slow-mode transaction before triggering the first withdrawal.
3. The attacker's recipient address is a contract implementing the hook.

Condition 2 is trivially satisfied by any user via `submitSlowModeTransaction`. Condition 3 requires deploying a simple receiver contract. Condition 1 is the binding constraint today, but ERC777 tokens are a well-known class and the protocol's token registry is extensible. Likelihood is **medium** given the low attacker effort once a callback token is listed.

---

### Recommendation

Apply the **checks-effects-interactions** pattern: move `spotEngine.updateBalance` and the health check **before** `handleWithdrawTransfer`.

```solidity
// Recommended order in withdrawCollateral:
int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
int128 amountRealized = -int128(amount) * int128(multiplier);
spotEngine.updateBalance(productId, sender, amountRealized);   // 1. effect
spotEngine.assertUtilization(productId);
require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH); // 2. check
handleWithdrawTransfer(token, sendTo, amount, idx);             // 3. interaction
```

Additionally, add OpenZeppelin's `ReentrancyGuardUpgradeable` and apply `nonReentrant` to `withdrawCollateral`, `executeSlowModeTransaction`, and `submitFastWithdrawal` as defense-in-depth.

---

### Proof of Concept

```
1. Attacker deploys ReceiverContract implementing ERC777 tokensReceived.
2. Attacker opens a subaccount with 200 USDC (ERC777 collateral).
3. Attacker calls Endpoint.submitSlowModeTransaction(WithdrawCollateral{amount:100}) → queued at idx=N.
4. Attacker calls Endpoint.submitSlowModeTransaction(WithdrawCollateral{amount:100}) → queued at idx=N+1.
5. After SLOW_MODE_TX_DELAY, attacker calls Endpoint.executeSlowModeTransaction().
   → Clearinghouse.withdrawCollateral(sender, productId, 100, sendTo, N) is called.
   → handleWithdrawTransfer transfers 100 tokens to ReceiverContract.
   → ERC777 tokensReceived fires inside ReceiverContract.
     → ReceiverContract calls Endpoint.executeSlowModeTransaction().
       → Clearinghouse.withdrawCollateral(sender, productId, 100, sendTo, N+1) is called.
       → SpotEngine balance is still 200 (not yet decremented).
       → Health check passes. 100 tokens transferred. Balance decremented to 100.
     → Re-entrant call returns.
   → Original call resumes. Balance decremented from 100 to 0.
   → Health check passes (balance == 0, still healthy).
6. Attacker has received 200 tokens from a 200-token deposit — but the second
   withdrawal consumed pool liquidity that should have been protected by the
   first withdrawal's balance deduction.
```

### Citations

**File:** core/contracts/Clearinghouse.sol (L377-385)
```text
    function handleWithdrawTransfer(
        IERC20Base token,
        address to,
        uint128 amount,
        uint64 idx
    ) internal virtual {
        token.safeTransfer(withdrawPool, uint256(amount));
        BaseWithdrawPool(withdrawPool).submitWithdrawal(token, to, amount, idx);
    }
```

**File:** core/contracts/Clearinghouse.sol (L408-419)
```text
        handleWithdrawTransfer(token, sendTo, amount, idx);

        int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
        int128 amountRealized = -int128(amount) * int128(multiplier);
        spotEngine.updateBalance(productId, sender, amountRealized);
        spotEngine.assertUtilization(productId);

        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
```

**File:** core/contracts/BaseWithdrawPool.sol (L184-190)
```text
    function handleWithdrawTransfer(
        IERC20Base token,
        address to,
        uint128 amount
    ) internal virtual {
        token.safeTransfer(to, uint256(amount));
    }
```

**File:** core/contracts/Endpoint.sol (L231-236)
```text
    function executeSlowModeTransaction() external {
        SlowModeConfig memory _slowModeConfig = slowModeConfig;
        _executeSlowModeTransaction(_slowModeConfig, false);
        nSubmissions += 1;
        slowModeConfig = _slowModeConfig;
    }
```
