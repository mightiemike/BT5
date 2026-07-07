### Title
CEI Violation in `withdrawCollateral` Enables Reentrancy via Callback Token — (`File: core/contracts/Clearinghouse.sol`)

---

### Summary

`Clearinghouse.withdrawCollateral` performs the full token transfer to the recipient **before** decrementing the sender's balance in `SpotEngine`. If a supported collateral token implements a transfer callback (e.g., ERC-777 `tokensReceived`), a malicious recipient contract can reenter the withdrawal flow through a second queued slow-mode transaction, withdrawing more collateral than the subaccount actually holds.

---

### Finding Description

In `Clearinghouse.withdrawCollateral` (lines 391–421), the execution order is:

1. **Line 408** — `handleWithdrawTransfer(token, sendTo, amount, idx)` is called first. This internally calls `token.safeTransfer(withdrawPool, ...)` and then `BaseWithdrawPool.submitWithdrawal(...)`, which calls `token.safeTransfer(sendTo, ...)`. Both are external calls that transfer real tokens to the recipient.

2. **Line 412** — `spotEngine.updateBalance(productId, sender, amountRealized)` is called **after** the transfer. This is the state update that decrements the subaccount's collateral balance. [1](#0-0) 

The `handleWithdrawTransfer` helper in `Clearinghouse` first pushes tokens to the withdraw pool, then calls `submitWithdrawal` on it: [2](#0-1) 

`BaseWithdrawPool.submitWithdrawal` then calls `handleWithdrawTransfer` which calls `token.safeTransfer(to, ...)`, delivering tokens to the recipient: [3](#0-2) [4](#0-3) 

At the moment the recipient's callback fires, `spotEngine.updateBalance` has not yet been called, so the subaccount's balance in the engine still reflects the full pre-withdrawal amount.

Neither `withdrawCollateral` nor `Endpoint.executeSlowModeTransaction` carries a `nonReentrant` guard: [5](#0-4) 

---

### Impact Explanation

An attacker who controls a recipient contract and has queued two slow-mode `WithdrawCollateral` transactions for the same subaccount (each for the full balance) can:

1. Trigger execution of the first withdrawal.
2. Receive a token callback during `token.safeTransfer(sendTo, ...)`.
3. From inside the callback, call `Endpoint.executeSlowModeTransaction` to execute the second queued withdrawal.
4. Because `spotEngine.updateBalance` has not yet run for the first withdrawal, the health check at line 419 still sees the full balance and passes.
5. Both withdrawals succeed, draining **2× the deposited collateral** from the protocol.

The corrupted state delta is: `spotEngine` balance for the subaccount is decremented only once (or not at all if the second withdrawal reverts the first via a later health check), while two full token transfers have already been executed. This is a direct collateral theft from the protocol's token reserves. [6](#0-5) 

---

### Likelihood Explanation

**Medium.** The attack requires a collateral token with a transfer callback (ERC-777 or a custom hook). Standard ERC-20 tokens (USDC, WETH) do not have callbacks, so the vulnerability is dormant for those. However, the protocol is designed to support multiple collateral tokens via `spotEngine.getConfig(productId).token`, and any future or current listing of a callback-capable token activates this path. The attacker entry point — `Endpoint.executeSlowModeTransaction` — is fully permissionless and callable by any address after the slow-mode timeout. [5](#0-4) [7](#0-6) 

---

### Recommendation

Apply the Checks-Effects-Interactions pattern: move `spotEngine.updateBalance` and the health check **before** `handleWithdrawTransfer`. Alternatively, add a `nonReentrant` modifier (OpenZeppelin `ReentrancyGuard`) to `withdrawCollateral` and to `Endpoint.executeSlowModeTransaction`.

---

### Proof of Concept

```
Attacker setup:
  1. Deploy MaliciousRecipient contract implementing ERC-777 tokensReceived hook.
  2. Deposit 1000 USDT-777 (ERC-777 collateral) into subaccount.
  3. Queue slow-mode WithdrawCollateral tx #A: amount=1000, sendTo=MaliciousRecipient.
  4. Queue slow-mode WithdrawCollateral tx #B: amount=1000, sendTo=MaliciousRecipient.
  5. Wait for SLOW_MODE_TX_DELAY.

Attack execution:
  6. Call Endpoint.executeSlowModeTransaction() → processes tx #A.
  7. Clearinghouse.withdrawCollateral() calls handleWithdrawTransfer().
  8. Tokens arrive at MaliciousRecipient; tokensReceived() fires.
  9. Inside tokensReceived(), call Endpoint.executeSlowModeTransaction() → processes tx #B.
 10. spotEngine still shows balance=1000 (tx #A hasn't updated it yet).
 11. Health check passes; tx #B transfers another 1000 tokens to MaliciousRecipient.
 12. Control returns to tx #A; spotEngine.updateBalance(-1000) runs.
 13. Net result: 2000 tokens withdrawn against 1000 deposited.
``` [8](#0-7) [9](#0-8)

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

**File:** core/contracts/Clearinghouse.sol (L391-420)
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
```

**File:** core/contracts/BaseWithdrawPool.sol (L81-113)
```text
    function submitFastWithdrawal(
        uint64 idx,
        bytes calldata transaction,
        bytes[] calldata signatures
    ) public {
        require(!markedIdxs[idx], "Withdrawal already submitted");
        require(idx > minIdx, "idx too small");
        markedIdxs[idx] = true;

        Verifier v = Verifier(verifier);
        v.requireValidTxSignatures(transaction, idx, signatures);

        (
            uint32 productId,
            address sendTo,
            uint128 transferAmount
        ) = resolveFastWithdrawal(transaction);
        IERC20Base token = getToken(productId);

        require(transferAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);

        int128 fee = fastWithdrawalFeeAmount(token, productId, transferAmount);

        if (sendTo == msg.sender) {
            require(transferAmount > uint128(fee), "Fee larger than balance");
            transferAmount -= uint128(fee);
        } else {
            safeTransferFrom(token, msg.sender, uint128(fee));
        }

        fees[productId] += fee;

        handleWithdrawTransfer(token, sendTo, transferAmount);
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
