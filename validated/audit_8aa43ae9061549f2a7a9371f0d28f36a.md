### Title
Reentrancy via CEI Violation in `withdrawCollateral` Enables Double-Withdrawal Against Cross-Collateralized Subaccounts — (File: `core/contracts/Clearinghouse.sol`)

---

### Summary

`Clearinghouse.withdrawCollateral` performs external calls via `handleWithdrawTransfer` — including a token `transfer` and a call to `withdrawPool.submitWithdrawal` — **before** updating the subaccount's on-chain balance. This violates the Checks-Effects-Interactions (CEI) pattern and creates a reentrancy window. An attacker holding a cross-collateralized subaccount (e.g., ETH + USDC) can exploit a token with transfer hooks (ERC-777 or equivalent) to execute a second withdrawal against the still-unmodified balance, draining more of one asset than the subaccount actually holds, with the deficit covered by the remaining collateral passing the post-hoc health check.

---

### Finding Description

In `Clearinghouse.withdrawCollateral`:

```
handleWithdrawTransfer(token, sendTo, amount, idx);          // ← external call (line 408)

int128 amountRealized = -int128(amount) * int128(multiplier);
spotEngine.updateBalance(productId, sender, amountRealized); // ← state update (line 412)
spotEngine.assertUtilization(productId);
require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
```

`handleWithdrawTransfer` executes two external calls before any balance mutation:

1. `token.safeTransfer(withdrawPool, uint256(amount))` — a low-level `call` to the token contract.
2. `BaseWithdrawPool(withdrawPool).submitWithdrawal(token, to, amount, idx)` — a call to the withdraw pool passing the user's address as `to`.

If the token implements ERC-777 (or any transfer-hook standard), the `tokensReceived` callback fires on `withdrawPool` during step 1. If `submitWithdrawal` in step 2 notifies or calls back to `to` (the user's address), the attacker's contract receives control while `sender`'s balance in `SpotEngine` is still at its pre-withdrawal value.

During that callback window, the attacker calls `Endpoint.executeSlowModeTransaction()` (publicly callable, no reentrancy guard) to execute a second pre-queued withdrawal (W2) for the same asset and amount. W2 reads the same unmodified balance, passes its own health check, and completes. Control returns to W1, which then decrements the balance a second time, producing a negative balance for that asset. The final health check in W1 passes only if the subaccount's remaining collateral (e.g., ETH) covers the deficit — which it does for a cross-collateralized account.

---

### Impact Explanation

An attacker with a cross-collateralized subaccount (holding asset A as collateral and asset B to withdraw) can withdraw **2× their actual asset B balance** in a single transaction. The clearinghouse's token reserve for asset B is drained by the extra amount; the attacker's subaccount ends up with a negative asset B balance covered by asset A. This constitutes direct collateral theft from the protocol's token reserves, corrupting the `SpotEngine` balance invariant (`sum of all balances == clearinghouse token balance`) for the affected product.

---

### Likelihood Explanation

The attack requires:
1. A token registered in `SpotEngine` that implements ERC-777 or a similar transfer-hook mechanism (realistic as the protocol is designed to support multiple assets).
2. The attacker having a second slow-mode withdrawal queued and executable.
3. The attacker holding sufficient cross-collateral to pass the post-hoc health check after the double debit.

All three conditions are achievable by an unprivileged user with no special access. The entry path (`Endpoint.executeSlowModeTransaction` → `Clearinghouse.withdrawCollateral`) is fully permissionless.

---

### Recommendation

Apply the CEI pattern: update `spotEngine.updateBalance` and run `assertUtilization` and `getHealth` **before** calling `handleWithdrawTransfer`. Additionally, add OpenZeppelin's `ReentrancyGuardUpgradeable` to `Clearinghouse` and apply `nonReentrant` to `withdrawCollateral`.

```solidity
// Corrected order:
int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
int128 amountRealized = -int128(amount) * int128(multiplier);
spotEngine.updateBalance(productId, sender, amountRealized);   // effects first
spotEngine.assertUtilization(productId);
require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);

handleWithdrawTransfer(token, sendTo, amount, idx);            // interaction last
```

---

### Proof of Concept

**Setup:**
- Attacker subaccount holds 100 USDC + 10 ETH (ETH worth >100 USDC at oracle price).
- Attacker queues two slow-mode `WithdrawCollateral` transactions (W1, W2), each for 100 USDC.
- Attacker deploys a contract `AttackerHook` registered as ERC-777 operator/recipient on `withdrawPool`.

**Execution:**
1. Attacker calls `Endpoint.executeSlowModeTransaction()` → W1 dispatches → `Clearinghouse.withdrawCollateral(sender, USDC, 100, ...)`.
2. `handleWithdrawTransfer` calls `token.safeTransfer(withdrawPool, 100)`.
3. ERC-777 `tokensReceived` fires on `withdrawPool`; `AttackerHook` gains control.
4. `AttackerHook` calls `Endpoint.executeSlowModeTransaction()` → W2 dispatches → `Clearinghouse.withdrawCollateral(sender, USDC, 100, ...)`.
5. W2 reads USDC balance = 100 (unchanged). Sends 100 USDC to `withdrawPool`. Updates balance to 0. Health check: ETH covers. W2 succeeds.
6. Control returns to W1. `spotEngine.updateBalance` sets USDC balance = 0 − 100 = −100.
7. Health check: ETH (10 × price) − 100 USDC > 0. W1 succeeds.
8. **Result:** Attacker's subaccount has −100 USDC and 10 ETH; `withdrawPool` received 200 USDC from a subaccount that only held 100 USDC. Protocol's USDC reserve is short by 100 USDC.

---

**Root cause lines:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** core/contracts/Clearinghouse.sol (L408-412)
```text
        handleWithdrawTransfer(token, sendTo, amount, idx);

        int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
        int128 amountRealized = -int128(amount) * int128(multiplier);
        spotEngine.updateBalance(productId, sender, amountRealized);
```

**File:** core/contracts/libraries/ERC20Helper.sol (L9-21)
```text
    function safeTransfer(
        IERC20Base self,
        address to,
        uint256 amount
    ) internal {
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(IERC20Base.transfer.selector, to, amount)
        );
        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
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
