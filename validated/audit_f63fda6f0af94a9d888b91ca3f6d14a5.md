### Title
Rebasing Token Balance Desync Between Clearinghouse Custody and Internal Accounting Breaks Withdrawal Invariant — (File: `core/contracts/Clearinghouse.sol`)

---

### Summary

The Nado `Clearinghouse` holds ERC20 collateral tokens and tracks user balances via normalized internal accounting in `SpotEngine`. The protocol assumes a static 1:1 correspondence between the actual token balance held by the `Clearinghouse` and the sum of all internally recorded deposits minus withdrawals. This invariant is broken for rebasing tokens: a negative rebase causes withdrawal reverts (DoS), while a positive rebase permanently locks excess tokens with no recovery path.

---

### Finding Description

When a user deposits a spot collateral token, the `Endpoint` performs a `safeTransferFrom` to pull `txn.amount` tokens into the `Clearinghouse`, and then calls `Clearinghouse.depositCollateral`, which records exactly `txn.amount` (scaled by decimals) into the `SpotEngine`'s normalized balance accounting: [1](#0-0) 

The internal accounting in `SpotEngine` tracks `totalDepositsNormalized` and `totalBorrowsNormalized` via cumulative multipliers, but these values are only ever updated through explicit `updateBalance` calls — never by reading the actual on-chain token balance of the `Clearinghouse`: [2](#0-1) 

When a user withdraws, `withdrawCollateral` calls `handleWithdrawTransfer`, which unconditionally transfers the recorded `amount` from the `Clearinghouse` to the `WithdrawPool`: [3](#0-2) [4](#0-3) 

The `assertUtilization` check called after withdrawal only validates internal accounting consistency (`totalDeposits >= totalBorrows`), not the actual token balance held by the contract: [5](#0-4) 

Similarly, `manualAssert` only verifies that internal `SpotEngine` state hashes match the sequencer's view — it never compares actual ERC20 balances against internal totals: [6](#0-5) 

For a rebasing token registered as a spot product, the actual token balance held by the `Clearinghouse` can diverge from the internally recorded total at any time between deposit and withdrawal, without any on-chain mechanism to detect or correct the discrepancy.

---

### Impact Explanation

**Negative rebase scenario (loss of funds / DoS):**
1. User deposits 100 units of a rebasing token. `Clearinghouse` holds 100 tokens; `SpotEngine` records 100.
2. Token rebases down — `Clearinghouse` now holds 90 tokens, but accounting still shows 100.
3. User calls `withdrawCollateral(100)` → `handleWithdrawTransfer` calls `token.safeTransfer(withdrawPool, 100)` → **reverts** because the contract only holds 90.
4. All withdrawals for that product are bricked until the balance is somehow restored. Users cannot recover their collateral.

**Positive rebase scenario (stuck funds):**
1. User deposits 100 units. Token rebases up — `Clearinghouse` now holds 110 tokens, accounting shows 100.
2. User withdraws 100 → succeeds. The extra 10 tokens remain in the `Clearinghouse` permanently.
3. There is no `removeLiquidity` or sweep function on the `Clearinghouse` itself (unlike `BaseWithdrawPool.removeLiquidity`), so the excess is irrecoverable. [7](#0-6) 

---

### Likelihood Explanation

Any rebasing ERC20 token registered as a spot product via `SpotEngine.addOrUpdateProduct` is affected. The trigger requires no privileged access: any user depositing such a token and any subsequent rebase event (which is an autonomous, scheduled operation of the token contract) is sufficient. The sequencer path (`submitTransactionsChecked`) and the slow-mode path (`submitSlowModeTransaction`) both route through the same `Clearinghouse.depositCollateral` / `withdrawCollateral` logic, so both entry points are affected.

---

### Recommendation

1. **Preferred**: Explicitly disallow rebasing tokens from being registered as spot products in `SpotEngine.addOrUpdateProduct`.
2. **Alternative**: Use a balance-before/balance-after pattern in the deposit path to record the *actual* received amount rather than `txn.amount`, and track the total custodied balance separately from the internal accounting sum to detect and handle divergence.
3. Add a `sweepExcess` function on `Clearinghouse` (analogous to `BaseWithdrawPool.removeLiquidity`) to recover tokens that exceed the internally recorded total, preventing permanent lock of positive-rebase surplus.

---

### Proof of Concept

```
1. Deploy a rebasing ERC20 token (e.g., one whose `balanceOf` for all holders
   scales by a global factor on each rebase call).

2. Register it as a spot product via SpotEngine.addOrUpdateProduct().

3. User A deposits 1000e18 tokens:
   - Endpoint calls safeTransferFrom(userA, clearinghouse, 1000e18)
   - Clearinghouse.depositCollateral records amountRealized = 1000e18 * multiplier
   - SpotEngine.totalDepositsNormalized increases accordingly

4. Token contract executes a negative rebase of 10%:
   - clearinghouse.balanceOf(token) drops from 1000e18 to 900e18
   - SpotEngine internal accounting is unchanged (still shows 1000e18 equivalent)

5. User A submits WithdrawCollateral for 1000e18 via Endpoint (slow mode):
   - Clearinghouse.withdrawCollateral calls handleWithdrawTransfer(token, withdrawPool, 1000e18, idx)
   - handleWithdrawTransfer calls token.safeTransfer(withdrawPool, 1000e18)
   - REVERTS: ERC20 transfer amount exceeds balance (contract holds only 900e18)

6. assertUtilization passes (internal accounting is self-consistent), but the
   actual withdrawal is permanently blocked until external funds are injected.
```

### Citations

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

**File:** core/contracts/Clearinghouse.sol (L734-744)
```text
    function manualAssert(bytes calldata transaction) external view virtual {
        IEndpoint.ManualAssert memory txn = abi.decode(
            transaction[1:],
            (IEndpoint.ManualAssert)
        );
        require(txn.insurance == insurance, ERR_DSYNC);
        ISpotEngine spotEngine = _spotEngine();
        IPerpEngine perpEngine = _perpEngine();
        perpEngine.manualAssert(txn.perpStates);
        spotEngine.manualAssert(txn.spotStates);
    }
```

**File:** core/contracts/SpotEngineState.sol (L15-50)
```text
    function _updateBalanceNormalized(
        State memory state,
        BalanceNormalized memory balance,
        int128 balanceDelta
    ) internal pure {
        if (balance.amountNormalized > 0) {
            state.totalDepositsNormalized -= balance.amountNormalized;
        } else {
            state.totalBorrowsNormalized += balance.amountNormalized;
        }

        int128 cumulativeMultiplierX18;
        if (balance.amountNormalized > 0) {
            cumulativeMultiplierX18 = state.cumulativeDepositsMultiplierX18;
        } else {
            cumulativeMultiplierX18 = state.cumulativeBorrowsMultiplierX18;
        }

        int128 newAmount = balance.amountNormalized.mul(
            cumulativeMultiplierX18
        ) + balanceDelta;

        if (newAmount > 0) {
            cumulativeMultiplierX18 = state.cumulativeDepositsMultiplierX18;
        } else {
            cumulativeMultiplierX18 = state.cumulativeBorrowsMultiplierX18;
        }

        balance.amountNormalized = newAmount.div(cumulativeMultiplierX18);

        if (balance.amountNormalized > 0) {
            state.totalDepositsNormalized += balance.amountNormalized;
        } else {
            state.totalBorrowsNormalized -= balance.amountNormalized;
        }
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

**File:** core/contracts/BaseWithdrawPool.sol (L151-157)
```text
    function removeLiquidity(
        uint32 productId,
        uint128 amount,
        address sendTo
    ) external onlyOwner {
        handleWithdrawTransfer(getToken(productId), sendTo, amount);
    }
```
