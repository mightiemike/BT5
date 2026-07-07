### Title
CEI Violation in `withdrawCollateral` Enables Cross-Contract Reentrancy Double-Withdrawal via ERC777 Hook — (`File: core/contracts/Clearinghouse.sol`)

---

### Summary

`Clearinghouse.withdrawCollateral()` performs the token transfer to the recipient **before** decrementing the subaccount balance in `SpotEngine`. If the withdrawal token is ERC777 (or any token with a receive hook), a recipient contract can reenter `Endpoint.executeSlowModeTransaction()` during the transfer callback. Because neither `Clearinghouse` nor `Endpoint` shares a `nonReentrant` lock, and the SpotEngine balance has not yet been decremented, a second withdrawal processes against the original (un-decremented) balance, passing the health check and paying out twice.

---

### Finding Description

In `Clearinghouse.withdrawCollateral()`, the execution order is:

1. **Transfer first** — `handleWithdrawTransfer(token, sendTo, amount, idx)` is called at line 408, which pushes tokens through `WithdrawPool` to the recipient.
2. **State update second** — `spotEngine.updateBalance(productId, sender, amountRealized)` is called at line 412, only after the transfer completes.
3. **Health check last** — `require(getHealth(sender, healthType) >= 0, ...)` at line 419 runs after both. [1](#0-0) 

`handleWithdrawTransfer` first sends tokens to `WithdrawPool`, then calls `WithdrawPool.submitWithdrawal()`, which calls `handleWithdrawTransfer` on the pool, ultimately executing `token.safeTransfer(sendTo, amount)`. [2](#0-1) [3](#0-2) [4](#0-3) 

If `sendTo` is a contract and the token is ERC777, the `tokensReceived` hook fires **before** `withdrawCollateral` returns and before the SpotEngine balance is decremented. Inside that hook, the attacker calls `Endpoint.executeSlowModeTransaction()`, which is a public, unrestricted function with no `nonReentrant` guard. [5](#0-4) 

`executeSlowModeTransaction` calls `processSlowModeTransaction` → `EndpointTx.processSlowModeTransactionImpl`, which dispatches a pending `WithdrawCollateral` slow-mode transaction back into `Clearinghouse.withdrawCollateral()`. At this point:

- The SpotEngine balance for `sender` is still at its **original value** (not yet decremented).
- The `markedIdxs` guard in `WithdrawPool` uses the slow-mode `idx`, which is different from the sequencer-path `idx`, so it does not block the second withdrawal.
- The health check at line 419 passes because the balance appears intact.

The second withdrawal pays out in full. When execution unwinds back to the first call, the balance is decremented once — but two full withdrawals have been paid.

Neither `Clearinghouse` nor `Endpoint` imports or uses a shared `ReentrancyGuard`. They are separate contracts with independent state, exactly mirroring the cross-contract reentrancy class described in the reference report.

---

### Impact Explanation

A malicious recipient contract holding a pending slow-mode withdrawal can drain double the collateral per execution. Repeating the attack (by pre-queuing multiple slow-mode withdrawals) allows draining the `WithdrawPool` of all ERC777-denominated collateral. The corrupted state is the SpotEngine balance: it is decremented only once while two full withdrawals are paid, leaving the protocol insolvent for that token.

---

### Likelihood Explanation

ERC777 tokens are a supported token standard on EVM chains and are explicitly within scope of any protocol that accepts arbitrary ERC20-compatible tokens. The attacker needs only: (1) a supported ERC777 collateral token, (2) a pre-queued slow-mode `WithdrawCollateral` transaction (submitted via `Endpoint.submitSlowModeTransaction`, which is public), and (3) a contract address as the withdrawal recipient. No privileged access, sequencer compromise, or governance capture is required. The entry path is fully unprivileged.

---

### Recommendation

Follow the Checks-Effects-Interactions pattern in `Clearinghouse.withdrawCollateral()`: decrement the SpotEngine balance and perform the health check **before** calling `handleWithdrawTransfer`. Additionally, add a shared `nonReentrant` guard (or a cross-contract mutex stored in a shared storage slot) to `Clearinghouse.withdrawCollateral()` and `Endpoint.executeSlowModeTransaction()` to prevent cross-contract reentry.

```solidity
// Recommended order in withdrawCollateral():
int128 amountRealized = -int128(amount) * int128(multiplier);
spotEngine.updateBalance(productId, sender, amountRealized);   // 1. Effect
spotEngine.assertUtilization(productId);
require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH); // 2. Check
handleWithdrawTransfer(token, sendTo, amount, idx);             // 3. Interaction
```

---

### Proof of Concept

1. Attacker deploys a recipient contract `AttackerReceiver` that implements the ERC777 `tokensReceived` hook.
2. Attacker calls `Endpoint.depositCollateralWithReferral()` to deposit 100 units of an ERC777 token into subaccount `S`.
3. Attacker calls `Endpoint.submitSlowModeTransaction()` with a `WithdrawCollateral` transaction for 100 units, sending to `AttackerReceiver`. This queues a slow-mode tx at index `T`.
4. Sequencer processes a `WithdrawCollateral` transaction for subaccount `S` (100 units, `sendTo = AttackerReceiver`) via `submitTransactionsChecked`. This triggers `Clearinghouse.withdrawCollateral()`.
5. Inside `handleWithdrawTransfer`, `WithdrawPool.handleWithdrawTransfer` calls `token.safeTransfer(AttackerReceiver, 100)`.
6. ERC777 fires `AttackerReceiver.tokensReceived()`. At this moment, SpotEngine balance for `S` is still 100 (not yet decremented).
7. `AttackerReceiver` calls `Endpoint.executeSlowModeTransaction()`. The slow-mode `WithdrawCollateral` at index `T` is dispatched. `Clearinghouse.withdrawCollateral()` is entered again. SpotEngine balance is 100, health check passes. `WithdrawPool` pays out another 100 tokens to `AttackerReceiver`. `markedIdxs[T]` is set.
8. Execution returns to step 4. SpotEngine balance is decremented by 100 (to 0). Health check passes.
9. Net result: `AttackerReceiver` received 200 tokens; SpotEngine records a balance of 0. Protocol is short 100 tokens. [6](#0-5) [3](#0-2) [5](#0-4)

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

**File:** core/contracts/Clearinghouse.sol (L391-421)
```text
    function withdrawCollateral(
        bytes32 sender,
        uint32 productId,
        uint128 amount,
        address sendTo,
        uint64 idx
    ) public virtual onlyEndpoint {
        require(!RiskHelper.isIsolatedSubaccount(sender), ERR_UNAUTHORIZED);
        require(amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        ISpotEngine spotEngine = _spotEngine();
        IERC20Base token = IERC20Base(spotEngine.getConfig(productId).token);
        require(address(token) != address(0));

        if (sendTo == address(0)) {
            sendTo = address(uint160(bytes20(sender)));
        }

        handleWithdrawTransfer(token, sendTo, amount, idx);

        int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
        int128 amountRealized = -int128(amount) * int128(multiplier);
        spotEngine.updateBalance(productId, sender, amountRealized);
        spotEngine.assertUtilization(productId);

        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
        emit ModifyCollateral(amountRealized, sender, productId);
    }
```

**File:** core/contracts/BaseWithdrawPool.sol (L116-132)
```text
    function submitWithdrawal(
        IERC20Base token,
        address sendTo,
        uint128 amount,
        uint64 idx
    ) public {
        require(msg.sender == clearinghouse);

        if (markedIdxs[idx]) {
            return;
        }
        markedIdxs[idx] = true;
        // set minIdx to most recent withdrawal submitted by sequencer
        minIdx = idx;

        handleWithdrawTransfer(token, sendTo, amount);
    }
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
