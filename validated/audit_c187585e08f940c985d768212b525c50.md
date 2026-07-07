### Title
Rebasing Token Rewards Permanently Locked in Clearinghouse Due to Absolute-Amount Internal Balance Tracking — (File: `core/contracts/Clearinghouse.sol`)

---

### Summary

The `Clearinghouse.depositCollateral` function credits users with exactly the deposited `amount` in the internal `SpotEngine` balance system. For rebasing tokens (e.g., stETH, aTokens), the actual ERC20 balance held by the Clearinghouse grows over time through protocol-level rebases, but the internal accounting never reflects this growth. The `assertUtilization` check — which explicitly guards against withdrawing tokens not tracked in internal balances — then permanently locks all rebasing gains inside the Clearinghouse with no recovery path.

---

### Finding Description

When a user deposits collateral via `Endpoint.depositCollateral` → `Clearinghouse.depositCollateral`, the function credits the user's internal balance with exactly `txn.amount` scaled by a decimal multiplier:

```solidity
// Clearinghouse.sol line 205-207
int128 amountRealized = int128(txn.amount) * int128(multiplier);
spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
```

This calls `SpotEngineState._updateBalanceNormalized`, which stores the balance as a normalized share (`amountNormalized = newAmount / cumulativeDepositsMultiplierX18`). The `cumulativeDepositsMultiplierX18` only grows through interest accrual from borrowers — it has no mechanism to reflect external token balance increases caused by rebasing.

If a rebasing token is listed as a product (nothing in `addOrUpdateProduct` or `SpotEngine.initialize` prohibits this), the Clearinghouse's actual ERC20 balance will grow beyond what the internal accounting tracks. When a user attempts to withdraw, `withdrawCollateral` transfers tokens and then calls `assertUtilization`:

```solidity
// SpotEngine.sol lines 232-241
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

This check compares internal accounting figures only — it does not compare against `token.balanceOf(clearinghouse)`. The comment directly above `assertUtilization` in `SpotEngine.sol` explicitly states the design intent:

```solidity
// only check on withdraw -- ensure that users can't withdraw
// funds that are in the Nado contract but not officially
// 'deposited' into the Nado system and counted in balances
// (i.e. if a user transfers tokens to the clearinghouse
// without going through the standard deposit)
```

This comment confirms that tokens present in the Clearinghouse but not tracked in internal balances — exactly the category that rebasing gains fall into — are intentionally blocked from withdrawal. There is no function in the codebase that reads `token.balanceOf(clearinghouse)`, computes the surplus over internal accounting, and credits it to any account (including `X_ACCOUNT` or `FEES_ACCOUNT`).

---

### Impact Explanation

Any rebasing token listed as a spot product will accumulate rebasing gains inside the Clearinghouse that no user or protocol account can ever withdraw. The gains are not credited to depositors, not credited to `X_ACCOUNT`, and not recoverable via any existing function. The loss is permanent and grows monotonically with each rebase event. All depositors of that token are affected proportionally to their deposit size and the duration of their deposit.

---

### Likelihood Explanation

The protocol does not restrict which ERC20 tokens can be listed as products. `SpotEngine.addOrUpdateProduct` accepts any `Config.token` address without any rebasing-token guard. If the protocol lists a rebasing token (e.g., stETH on Ink Chain, or any yield-bearing token), the vulnerability is triggered automatically and continuously by the token's own rebase mechanism — no attacker action is required beyond the initial deposit.

---

### Recommendation

1. **Track shares instead of absolute amounts for rebasing tokens**: Before crediting `amountRealized` to the user's internal balance, snapshot `token.balanceOf(clearinghouse)` before and after the `transferFrom`, and credit the actual received delta rather than the nominal `txn.amount`. This handles fee-on-transfer tokens as well.

2. **Alternatively, explicitly prohibit rebasing tokens**: Add a check in `addOrUpdateProduct` or `depositCollateral` that rejects known rebasing tokens, and document that only wrapped non-rebasing equivalents (e.g., wstETH instead of stETH) are supported.

---

### Proof of Concept

1. A rebasing token `rTKN` (e.g., stETH analog) is listed as product ID `42` in `SpotEngine`.
2. Alice calls `Endpoint.depositCollateral(subaccountName, 42, 100e18)`.
   - `handleDepositTransfer` pulls 100e18 `rTKN` from Alice into the Clearinghouse.
   - `Clearinghouse.depositCollateral` credits Alice's internal balance with `100e18 * multiplier`.
3. `rTKN` rebases: Clearinghouse now holds `105e18 rTKN`, but Alice's internal balance is still `100e18 * multiplier`.
4. Alice calls `Endpoint` to withdraw `105e18 rTKN`.
   - `withdrawCollateral` attempts to debit `105e18 * multiplier` from Alice's balance, making it negative.
   - `assertUtilization` fails (or the health check fails), reverting the transaction.
5. Alice can only withdraw `100e18 rTKN`. The `5e18 rTKN` rebase gain is permanently locked in the Clearinghouse.
6. No admin function, no `X_ACCOUNT` rebalance, and no `manualAssert` path can recover these tokens. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** core/contracts/SpotEngine.sol (L227-241)
```text
    // only check on withdraw -- ensure that users can't withdraw
    // funds that are in the Nado contract but not officially
    // 'deposited' into the Nado system and counted in balances
    // (i.e. if a user transfers tokens to the clearinghouse
    // without going through the standard deposit)
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
