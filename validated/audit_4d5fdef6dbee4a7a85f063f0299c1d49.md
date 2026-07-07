### Title
Protocol Spot Markets Are Incompatible With Rebasing Tokens — (`core/contracts/SpotEngine.sol`, `core/contracts/Clearinghouse.sol`)

---

### Summary

Nado's `SpotEngine` stores user collateral as fixed normalized amounts (`amountNormalized`) that are never reconciled against the actual on-chain token balance. The solvency guard `assertUtilization` only validates internal accounting invariants, not the real token balance held by the `Clearinghouse`. If a rebasing token is configured as a spot product's collateral asset, a negative rebase silently creates an undercollateralized state: users who withdraw later cannot receive their full balance, and the shortfall is undetectable by any on-chain check until a transfer reverts.

---

### Finding Description

**Deposit path — fixed balance stored:**

When a user deposits collateral, `Endpoint.depositCollateralWithReferral` transfers the exact `amount` of tokens from the caller to the `Clearinghouse`, then enqueues a slow-mode `DepositCollateral` transaction. [1](#0-0) 

When the sequencer processes that transaction, `Clearinghouse.depositCollateral` converts the raw token amount to an 18-decimal normalized value and writes it into `SpotEngine` as a fixed `amountNormalized`: [2](#0-1) 

The normalized balance is stored in `SpotEngineState.balances` and is only ever updated by explicit protocol actions (trades, withdrawals, liquidations). It has no mechanism to track changes in the underlying token's actual balance. [3](#0-2) 

**Withdrawal path — solvency check is accounting-only:**

`Clearinghouse.withdrawCollateral` first transfers tokens to the `WithdrawPool`, then decrements the internal balance, and finally calls `spotEngine.assertUtilization`: [4](#0-3) 

`assertUtilization` checks only that the internal `totalDepositsNormalized * cumulativeDepositsMultiplierX18 >= totalBorrowsNormalized * cumulativeBorrowsMultiplierX18`: [5](#0-4) 

This invariant is computed entirely from internal state. It never reads `token.balanceOf(address(this))`. There is no check that the Clearinghouse's actual token balance is sufficient to cover the net amount owed to depositors.

**Consequence of a negative rebase:**

If the collateral token negatively rebases (e.g., AMPL supply contraction), the Clearinghouse's real token balance decreases while `totalDepositsNormalized` is unchanged. `assertUtilization` continues to pass. Users who withdraw first receive their full amount. Once the real balance is exhausted, subsequent `token.safeTransfer` calls in `handleWithdrawTransfer` revert: [6](#0-5) 

The remaining users hold internal balances they can never redeem. The shortfall is invisible to the protocol until the transfer reverts.

**Consequence of a positive rebase:**

If the token positively rebases, extra tokens accumulate in the Clearinghouse. No user's `amountNormalized` increases, so no user can withdraw the surplus. The tokens are effectively locked. The `X_ACCOUNT` rebalance path (`rebalanceXWithdraw`) could theoretically extract them, but only via sequencer/owner action — not a user-accessible remedy. [7](#0-6) 

---

### Impact Explanation

For a negative rebase: depositors who have not yet withdrawn lose the rebased portion of their collateral. The Clearinghouse becomes insolvent relative to its internal ledger. Withdrawals fail with a transfer revert once the real balance is exhausted. The `assertUtilization` guard does not prevent this because it never reads the actual token balance. This is a direct, permanent loss of user funds proportional to the magnitude of the negative rebase.

For a positive rebase: the rebased surplus is permanently stranded in the Clearinghouse, inaccessible to depositors whose internal balances were not updated.

---

### Likelihood Explanation

The `SpotEngine.addOrUpdateProduct` function accepts any ERC20-compatible token address with no restriction on rebasing behavior: [8](#0-7) 

The `IERC20Base` interface imposes no constraints beyond standard ERC20 methods: [9](#0-8) 

There is no token whitelist, blacklist, or documentation warning against rebasing tokens. Any product configuration that includes a rebasing token (e.g., AMPL, aTokens, elastic supply tokens) immediately activates this vulnerability. No attacker action is required — the rebase event itself is the trigger.

---

### Recommendation

1. **Add a real-balance solvency check in `assertUtilization`**: After computing `totalDeposits - totalBorrows`, verify that `token.balanceOf(address(clearinghouse)) >= netOwed`. This catches negative-rebase insolvency before it silently accumulates.

2. **Document the incompatibility**: Explicitly state in `addOrUpdateProduct` and protocol documentation that rebasing tokens are unsupported as spot collateral assets.

3. **Consider a token validation hook**: In `addOrUpdateProduct`, optionally enforce that the token's total supply is fixed or that it implements a non-rebasing interface.

---

### Proof of Concept

1. Protocol owner calls `SpotEngine.addOrUpdateProduct` with AMPL as the collateral token for `productId = 5`.
2. User A deposits 1,000 AMPL via `Endpoint.depositCollateralWithReferral`. The Clearinghouse receives 1,000 AMPL. `SpotEngine` records `amountNormalized` equivalent to 1,000 AMPL.
3. AMPL undergoes a −10% negative rebase. The Clearinghouse's real balance drops to 900 AMPL. `totalDepositsNormalized` is unchanged.
4. User A submits a withdrawal for 1,000 AMPL. The sequencer processes it via `Clearinghouse.withdrawCollateral`.
5. `handleWithdrawTransfer` calls `token.safeTransfer(withdrawPool, 1000e18)`. The Clearinghouse only holds 900 AMPL — the transfer reverts. [10](#0-9) 
6. User A retries with 900 AMPL. The transfer succeeds. `assertUtilization` passes (internal accounting: 100 AMPL deposited, 0 borrowed). User A's internal balance now shows 100 AMPL.
7. User A attempts to withdraw the remaining 100 AMPL. The Clearinghouse has 0 AMPL. The transfer reverts again.
8. User A has permanently lost 100 AMPL (the rebased shortfall). The internal ledger shows a 100 AMPL balance that can never be redeemed.

### Citations

**File:** core/contracts/Endpoint.sol (L144-148)
```text
        handleDepositTransfer(
            IERC20Base(spotEngine.getToken(productId)),
            msg.sender,
            uint256(amount)
        );
```

**File:** core/contracts/Clearinghouse.sol (L199-208)
```text
        require(txn.amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        ISpotEngine spotEngine = _spotEngine();
        uint8 decimals = _decimals(txn.productId);

        require(decimals <= MAX_DECIMALS);
        int256 multiplier = int256(10**(MAX_DECIMALS - decimals));
        int128 amountRealized = int128(txn.amount) * int128(multiplier);

        spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
        emit ModifyCollateral(amountRealized, txn.sender, txn.productId);
```

**File:** core/contracts/Clearinghouse.sol (L327-343)
```text
    function rebalanceXWithdraw(bytes calldata transaction, uint64 nSubmissions)
        external
        onlyEndpoint
    {
        IEndpoint.RebalanceXWithdraw memory txn = abi.decode(
            transaction[1:],
            (IEndpoint.RebalanceXWithdraw)
        );

        withdrawCollateral(
            X_ACCOUNT,
            txn.productId,
            txn.amount,
            txn.sendTo,
            nSubmissions
        );
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

**File:** core/contracts/Clearinghouse.sol (L408-413)
```text
        handleWithdrawTransfer(token, sendTo, amount, idx);

        int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
        int128 amountRealized = -int128(amount) * int128(multiplier);
        spotEngine.updateBalance(productId, sender, amountRealized);
        spotEngine.assertUtilization(productId);
```

**File:** core/contracts/SpotEngineState.sol (L12-13)
```text
    mapping(uint32 => mapping(bytes32 => BalanceNormalized)) internal balances;
    mapping(bytes32 => NlpLockedBalanceQueue) internal nlpLockedBalanceQueues;
```

**File:** core/contracts/SpotEngine.sol (L68-97)
```text
    function addOrUpdateProduct(
        uint32 productId,
        uint32 quoteId,
        int128 sizeIncrement,
        int128 minSize,
        Config calldata config,
        RiskHelper.RiskStore calldata riskStore
    ) public onlyOwner {
        bool isNewProduct = _addOrUpdateProduct(
            productId,
            quoteId,
            sizeIncrement,
            minSize,
            riskStore
        );
        configs[productId] = config;

        if (isNewProduct) {
            require(productId != QUOTE_PRODUCT_ID);
            _setState(
                productId,
                State({
                    cumulativeDepositsMultiplierX18: ONE,
                    cumulativeBorrowsMultiplierX18: ONE,
                    totalDepositsNormalized: 0,
                    totalBorrowsNormalized: 0
                })
            );
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

**File:** core/contracts/interfaces/IERC20Base.sol (L1-42)
```text
// SPDX-License-Identifier: GPL-2.0-or-later
pragma solidity ^0.8.0;

interface IERC20Base {
    function decimals() external view returns (uint8);

    /**
     * @dev Moves `amount` tokens from the caller's account to `to`.
     *
     * Returns a boolean value indicating whether the operation succeeded.
     *
     * Emits a {Transfer} event.
     */
    function transfer(address to, uint256 amount) external returns (bool);

    /**
     * @dev Moves `amount` tokens from `from` to `to` using the
     * allowance mechanism. `amount` is then deducted from the caller's
     * allowance.
     *
     * Returns a boolean value indicating whether the operation succeeded.
     *
     * Emits a {Transfer} event.
     */
    function transferFrom(
        address from,
        address to,
        uint256 amount
    ) external returns (bool);

    function increaseAllowance(address spender, uint256 addedValue)
        external
        returns (bool);

    function decreaseAllowance(address spender, uint256 subtractedValue)
        external
        returns (bool);

    function balanceOf(address account) external view returns (uint256);

    function approve(address spender, uint256 value) external returns (bool);
}
```
