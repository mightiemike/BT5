### Title
Fee-on-Transfer Token Deposit Over-Credits Internal Balance, Causing Withdrawal Failures - (File: `core/contracts/Clearinghouse.sol`)

---

### Summary

`depositCollateral()` in `Clearinghouse.sol` credits a user's internal SpotEngine balance using `txn.amount` — the caller-supplied parameter — rather than the actual tokens received by the contract. For fee-on-transfer ERC20 tokens, the contract receives fewer tokens than `txn.amount`, but the full `txn.amount` is credited. Over time, the sum of all credited balances exceeds the actual token holdings of the Clearinghouse, causing later `withdrawCollateral()` calls to revert due to insufficient token balance.

---

### Finding Description

The deposit flow proceeds as follows:

**Step 1 — Token transfer** (`EndpointStorage.sol`, `handleDepositTransfer`):

```solidity
function handleDepositTransfer(
    IERC20Base token,
    address from,
    uint256 amount
) internal {
    safeTransferFrom(token, from, amount);      // pulls `amount` from user
    safeTransferTo(token, address(clearinghouse), amount); // forwards `amount` to CH
}
```

For a fee-on-transfer token with a 1% fee, the Clearinghouse actually receives `amount * 0.99`, not `amount`. [1](#0-0) 

**Step 2 — Internal accounting** (`Clearinghouse.sol`, `depositCollateral`):

```solidity
int128 amountRealized = int128(txn.amount) * int128(multiplier);
spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
```

`amountRealized` is derived from `txn.amount` — the transaction parameter — not from the actual tokens received. The SpotEngine is credited the full `txn.amount`. [2](#0-1) 

**Step 3 — Withdrawal** (`Clearinghouse.sol`, `handleWithdrawTransfer`):

```solidity
function handleWithdrawTransfer(...) internal virtual {
    token.safeTransfer(withdrawPool, uint256(amount));
    BaseWithdrawPool(withdrawPool).submitWithdrawal(token, to, amount, idx);
}
```

The withdrawal transfers `amount` tokens out of the Clearinghouse based on internal accounting. If the Clearinghouse holds fewer tokens than the sum of all credited balances (due to accumulated fee-on-transfer discrepancies), the `safeTransfer` reverts. [3](#0-2) 

**Step 4 — `assertUtilization` does not catch this**:

```solidity
function assertUtilization(uint32 productId) external view {
    int128 totalDeposits = _state.totalDepositsNormalized.mul(...);
    int128 totalBorrows  = _state.totalBorrowsNormalized.mul(...);
    require(totalDeposits >= totalBorrows, ERR_MAX_UTILIZATION);
}
```

This check compares internal accounting against internal accounting only. It never reads `token.balanceOf(address(clearinghouse))`, so it cannot detect the growing shortfall between credited balances and actual holdings. [4](#0-3) 

---

### Impact Explanation

For any fee-on-transfer token listed as a spot collateral product, every deposit inflates the internal `totalDepositsNormalized` by more than the tokens actually received. The Clearinghouse's real token balance falls progressively below the sum of all user-credited balances. When users attempt to withdraw, `handleWithdrawTransfer` calls `token.safeTransfer(withdrawPool, amount)` using the internally-tracked amount. Once the real balance is exhausted, this call reverts, permanently blocking withdrawals for remaining depositors — a direct loss of funds for those users.

---

### Likelihood Explanation

The likelihood depends on whether a fee-on-transfer token is ever listed as a supported spot product. The protocol has no on-chain enforcement preventing such a listing; the restriction is purely an off-chain operational guideline. Any governance action, misconfiguration, or future market addition that lists a fee-on-transfer token activates this vulnerability immediately for every deposit made against that product. The entry path (`depositCollateralWithReferral` → `handleDepositTransfer` → `depositCollateral`) is fully permissionless and reachable by any user.

---

### Recommendation

Replace the use of `txn.amount` in `depositCollateral` with the actual tokens received. Measure the Clearinghouse's token balance before and after `handleDepositTransfer` and use the delta as `amountRealized`:

```solidity
uint256 before = token.balanceOf(address(this));
// transfer occurs in Endpoint before this call
uint256 actualReceived = token.balanceOf(address(this)) - before;
int128 amountRealized = int128(uint128(actualReceived)) * int128(multiplier);
```

Alternatively, add an on-chain check in `addProduct` / product configuration to reject tokens whose `transfer` delivers less than the specified amount (i.e., enforce no-fee-on-transfer at listing time).

---

### Proof of Concept

1. A fee-on-transfer token `FOT` (1% fee) is listed as a spot collateral product with `productId = 5`.
2. Alice calls `depositCollateralWithReferral(aliceSubaccount, 5, 1000e18, "ref")`.
   - `handleDepositTransfer` pulls `1000e18` from Alice; Clearinghouse receives `990e18`.
   - `depositCollateral` credits Alice's SpotEngine balance with `1000e18`.
3. Bob does the same: Clearinghouse receives another `990e18`; Bob is credited `1000e18`.
   - Clearinghouse holds `1980e18` FOT; internal accounting shows `2000e18` total deposits.
4. Alice calls `withdrawCollateral` for `1000e18`.
   - `handleWithdrawTransfer` calls `token.safeTransfer(withdrawPool, 1000e18)` — succeeds (1980 ≥ 1000).
   - Clearinghouse now holds `980e18`.
5. Bob calls `withdrawCollateral` for `1000e18`.
   - `handleWithdrawTransfer` calls `token.safeTransfer(withdrawPool, 1000e18)` — **reverts** (980 < 1000).
   - Bob's funds are permanently locked. [5](#0-4) [1](#0-0) [6](#0-5)

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
