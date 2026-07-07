### Title
Re-entrancy via ERC777 Token Hook in `withdrawCollateral` Allows Double-Withdrawal Before Balance Update — (`File: core/contracts/Clearinghouse.sol`)

---

### Summary

`Clearinghouse.withdrawCollateral` transfers collateral tokens to the recipient **before** decrementing the subaccount's on-chain balance in `SpotEngine`. For tokens with transfer hooks (ERC777-style `_afterTokenTransfer`), a recipient contract can re-enter the withdrawal flow while the subaccount balance still reflects the pre-withdrawal amount, enabling a double-withdrawal that creates bad debt in the protocol.

---

### Finding Description

`Clearinghouse.withdrawCollateral` violates the Checks-Effects-Interactions (CEI) pattern. The token transfer is performed at line 408 via `handleWithdrawTransfer`, but the subaccount balance is not decremented until line 412 (`spotEngine.updateBalance`) and the health check is not enforced until line 419 (`getHealth`). [1](#0-0) 

`handleWithdrawTransfer` in `Clearinghouse` first moves tokens to the `WithdrawPool`, then calls `BaseWithdrawPool.submitWithdrawal`, which performs the final `token.safeTransfer(to, amount)` to the user: [2](#0-1) [3](#0-2) 

The ERC777-style hook fires at the `token.safeTransfer(to, amount)` call inside `BaseWithdrawPool.handleWithdrawTransfer`. At that moment, the call stack is still inside `Clearinghouse.withdrawCollateral` and `spotEngine.updateBalance` has **not yet been called**. The subaccount balance in `SpotEngine` still reflects the full pre-withdrawal amount.

By contrast, `withdrawInsurance` in the same contract correctly follows CEI — it decrements `insurance` **before** calling `handleWithdrawTransfer`: [4](#0-3) 

This inconsistency confirms the CEI violation in `withdrawCollateral` is not intentional.

---

### Impact Explanation

An attacker controlling a recipient contract with a `tokensReceived` hook can re-enter the withdrawal flow while the subaccount balance is stale. If the attacker has two valid queued `WithdrawCollateral` slow-mode transactions (each for amount `X`) and sufficient other collateral to satisfy the health check, the re-entry allows both withdrawals to pass the health check against the same un-decremented balance. The result is:

- Attacker withdraws `2X` collateral while only holding `X`.
- The protocol records a net negative balance (`-X`) for the subaccount in `SpotEngine`.
- This constitutes **bad debt** and **collateral theft** from the protocol's liquidity.

The `markedIdxs[idx]` guard in `BaseWithdrawPool.submitWithdrawal` blocks re-entry on the **same** `idx`: [5](#0-4) 

However, if `nSubmissions` is incremented before each slow-mode transaction is dispatched (making each withdrawal use a distinct `idx`), the re-entry uses a fresh `idx` and bypasses `markedIdxs`. The exact exploitability is gated on the `nSubmissions` increment order in `Endpoint.sol`, which was not available for review.

### Citations

**File:** core/contracts/Clearinghouse.sol (L283-291)
```text
        require(amount <= insurance, ERR_NO_INSURANCE);
        insurance -= amount;

        ISpotEngine spotEngine = _spotEngine();
        IERC20Base token = IERC20Base(
            spotEngine.getConfig(QUOTE_PRODUCT_ID).token
        );
        require(address(token) != address(0));
        handleWithdrawTransfer(token, txn.sendTo, txn.amount, idx);
```

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

**File:** core/contracts/Clearinghouse.sol (L407-419)
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

**File:** core/contracts/BaseWithdrawPool.sol (L124-131)
```text
        if (markedIdxs[idx]) {
            return;
        }
        markedIdxs[idx] = true;
        // set minIdx to most recent withdrawal submitted by sequencer
        minIdx = idx;

        handleWithdrawTransfer(token, sendTo, amount);
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
