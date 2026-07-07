### Title
Rebasing Token Rewards Permanently Stuck in Clearinghouse Due to Slow-Mode Deposit Delay — (`core/contracts/Endpoint.sol`, `core/contracts/EndpointStorage.sol`, `core/contracts/Clearinghouse.sol`)

---

### Summary

Nado's deposit flow immediately transfers the user-specified `amount` of tokens to `Clearinghouse`, then enqueues a `SlowModeTx` that records the same original `amount` for a deferred accounting credit (up to 3 days later). If a rebasing token such as stETH is listed as a supported collateral, the token balance held by `Clearinghouse` will silently grow during the slow-mode window, but only the original `amount` is ever credited to the depositor's subaccount. The surplus rebased tokens are permanently locked in `Clearinghouse` with no accounting entry and no recovery path.

---

### Finding Description

**Step 1 — Immediate token transfer, deferred accounting**

`Endpoint.depositCollateralWithReferral` calls `handleDepositTransfer`, which moves exactly `amount` tokens from the caller to `Clearinghouse` in the same transaction: [1](#0-0) 

Immediately after, a `SlowModeTx` is written to the queue, encoding the same original `amount`: [2](#0-1) 

The slow-mode delay is hardcoded to `SLOW_MODE_TX_DELAY` (3 days): [3](#0-2) 

**Step 2 — Rebasing occurs during the 3-day window**

While the `SlowModeTx` sits in the queue, a rebasing token's global share price increases (as Lido's accounting oracle does daily). `Clearinghouse`'s actual token balance grows to `amount + rebase_delta`, but no code path observes or records this delta.

**Step 3 — Accounting credit uses the stale original amount**

When the slow-mode tx is eventually executed, `Clearinghouse.depositCollateral` credits only the original `txn.amount`: [4](#0-3) 

`rebase_delta` is never credited to any subaccount.

**Step 4 — No recovery mechanism**

`Clearinghouse` has no sweep, rescue, or surplus-recovery function. The only outbound token paths are `handleWithdrawTransfer` (requires a matching accounting debit) and `withdrawInsurance` (quote token only). The rebased surplus is permanently inaccessible. [5](#0-4) 

**`assertUtilization` does not detect the discrepancy**

The utilization guard called on every withdrawal compares accounting totals only — it never reads the actual token balance of `Clearinghouse`: [6](#0-5) 

Similarly, `manualAssert` only hashes the accounting `State` structs, not the real on-chain token balance: [7](#0-6) 

---

### Impact Explanation

For every deposit of a rebasing token, the positive rebase accrued during the 3-day slow-mode window is silently absorbed by `Clearinghouse` and never attributed to any subaccount or insurance fund. Over time, with many depositors and daily rebases, the cumulative stuck amount grows proportionally to total rebasing-token TVL and the rebase rate. There is no on-chain mechanism to recover or redistribute these funds.

In the negative-rebase direction (which Lido states has not occurred but is not impossible), `Clearinghouse` would hold *less* than `amount` at execution time, causing `safeTransfer` to revert on subsequent withdrawals and potentially bricking the withdrawal path for that product.

---

### Likelihood Explanation

The trigger requires a rebasing token (e.g., stETH, aToken, or any balance-rebasing ERC-20) to be listed as a spot collateral product via `SpotEngine.addOrUpdateProduct`. The protocol uses a generic `IERC20Base` interface with no rebasing-token guard. Any operator listing such a token activates the bug for all depositors of that product. The 3-day slow-mode window is hardcoded and cannot be shortened by users.

---

### Recommendation

1. **Preferred**: Disallow rebasing tokens as collateral. Add a check in `addOrUpdateProduct` or `depositCollateralWithReferral` that rejects tokens whose `balanceOf` is not stable (e.g., require wrapping to wstETH before listing).
2. **Alternative**: Replace the fixed-`amount` slow-mode record with a balance-snapshot approach: record the actual `Clearinghouse` balance delta after the transfer, and use that delta as the credited amount when the slow-mode tx executes.

---

### Proof of Concept

1. Admin lists a rebasing token (e.g., stETH) as product `productId = 5`.
2. Alice calls `Endpoint.depositCollateral("alice\x00...", 5, 10 ether)`.
   - `handleDepositTransfer` moves `10 ether` of stETH from Alice → Endpoint → Clearinghouse.
   - `SlowModeTx` is queued with `amount = 10 ether`, `executableAt = now + 3 days`.
3. Lido's accounting oracle runs daily. Over 3 days the share price increases by 0.03%, so `Clearinghouse.balanceOf(stETH)` grows to `10.009 ether`.
4. After 3 days, anyone calls `Endpoint.executeSlowModeTransaction()`.
   - `Clearinghouse.depositCollateral` credits Alice with `amountRealized` derived from `txn.amount = 10 ether`.
   - `0.009 ether` of stETH remains in `Clearinghouse` with no accounting entry.
5. Alice can only withdraw `10 ether` worth of stETH. The `0.009 ether` surplus is permanently locked.

### Citations

**File:** core/contracts/EndpointStorage.sol (L111-119)
```text
    function handleDepositTransfer(
        IERC20Base token,
        address from,
        uint256 amount
    ) internal {
        require(address(token) != address(0), ERR_INVALID_PRODUCT);
        safeTransferFrom(token, from, amount);
        safeTransferTo(token, address(clearinghouse), amount);
    }
```

**File:** core/contracts/Endpoint.sol (L152-165)
```text
        slowModeTxs[_slowModeConfig.txCount++] = SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: abi.encodePacked(
                uint8(TransactionType.DepositCollateral),
                abi.encode(
                    DepositCollateral({
                        sender: subaccount,
                        productId: productId,
                        amount: amount
                    })
                )
            )
        });
```

**File:** core/contracts/Clearinghouse.sol (L193-209)
```text
    function depositCollateral(IEndpoint.DepositCollateral calldata txn)
        external
        virtual
        onlyEndpoint
    {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);
        require(txn.amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        ISpotEngine spotEngine = _spotEngine();
        uint8 decimals = _decimals(txn.productId);

        require(decimals <= MAX_DECIMALS);
        int256 multiplier = int256(10**(MAX_DECIMALS - decimals));
        int128 amountRealized = int128(txn.amount) * int128(multiplier);

        spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
        emit ModifyCollateral(amountRealized, txn.sender, txn.productId);
    }
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

**File:** core/contracts/SpotEngine.sol (L232-241)
```text
    function assertUtilization(uint32 productId) external view {
        (State memory _state, ) = getStateAndBalance(productId, X_ACCOUNT);
        int128 totalDeposits = _state.totalDepositsNormalized.mul(
            _state.cumulativeDepositsMultiplierX18
        );
        int128 totalBorrows = _state.totalBorrowsNormalized.mul(
            _state.cumulativeBorrowsMultiplierX18
        );
        require(totalDeposits >= totalBorrows, ERR_MAX_UTILIZATION);
    }
```

**File:** core/contracts/SpotEngine.sol (L279-288)
```text
    function manualAssert(bytes[] calldata _states) external view {
        for (uint128 i = 0; i < _states.length; ++i) {
            uint32 productId = productIds[i];
            require(
                keccak256(abi.encode(states[productId])) ==
                    keccak256(_states[i]),
                ERR_DSYNC
            );
        }
    }
```
