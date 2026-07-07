### Title
Rebasable Token Negative Rebase Causes Permanent Solvency Shortfall — (`File: core/contracts/SpotEngine.sol`, `File: core/contracts/Clearinghouse.sol`)

---

### Summary

The `assertUtilization` guard in `SpotEngine` only validates internal accounting consistency (`totalDeposits >= totalBorrows`) and never compares the net-deposit figure against the actual ERC-20 balance held by `Clearinghouse`. If a rebasable token is listed as a spot product and undergoes a negative rebase, the contract's real token balance silently falls below the sum of all recorded user balances. Early withdrawers drain the pool; later withdrawers receive nothing.

---

### Finding Description

**Root cause — `SpotEngine.assertUtilization`:**

```solidity
// core/contracts/SpotEngine.sol:232-241
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

The check is purely internal: it confirms that the protocol's own ledger has not over-borrowed. It never reads the actual ERC-20 balance of `Clearinghouse`.

**Withdrawal path — `Clearinghouse.withdrawCollateral`:**

```solidity
// core/contracts/Clearinghouse.sol:391-421
function withdrawCollateral(...) public virtual onlyEndpoint {
    ...
    handleWithdrawTransfer(token, sendTo, amount, idx);   // transfers real tokens
    ...
    spotEngine.updateBalance(productId, sender, amountRealized);
    spotEngine.assertUtilization(productId);              // only checks ledger
    ...
    require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
}
```

`handleWithdrawTransfer` calls `token.safeTransfer(withdrawPool, amount)` using the ledger-recorded amount. After a negative rebase the Clearinghouse holds fewer tokens than the ledger claims, but `assertUtilization` still passes because the ledger itself is internally consistent.

The `_balanceOf` helper exists in `Clearinghouse.sol` but is never invoked in the withdrawal path:

```solidity
// core/contracts/Clearinghouse.sol:387-389
function _balanceOf(address token) internal view virtual returns (uint128) {
    return uint128(IERC20Base(token).balanceOf(address(this)));
}
```

**Deposit path — `EndpointStorage.handleDepositTransfer`:**

```solidity
// core/contracts/EndpointStorage.sol:111-119
function handleDepositTransfer(IERC20Base token, address from, uint256 amount) internal {
    safeTransferFrom(token, from, amount);
    safeTransferTo(token, address(clearinghouse), amount);
}
```

Tokens are held in `Clearinghouse`. A rebase event directly changes `Clearinghouse`'s balance without any on-chain notification or ledger adjustment.

---

### Impact Explanation

After a negative rebase (supply contraction), the Clearinghouse holds `R` tokens where `R < totalDeposits - totalBorrows`. The invariant that the contract's real balance covers all net deposits is silently broken. The first users to call `withdrawCollateral` succeed and receive their full ledger amount, draining the pool. Subsequent users' `safeTransfer` calls revert because the pool is exhausted. The protocol's internal records permanently overstate the available collateral, and the shortfall cannot be recovered without an admin intervention that the protocol does not provide.

Corrupted state: `Clearinghouse` ERC-20 balance < `totalDepositsNormalized * cumulativeDepositsMultiplierX18 - totalBorrowsNormalized * cumulativeBorrowsMultiplierX18`.

---

### Likelihood Explanation

The protocol owner can list any ERC-20 token as a spot product via `SpotEngine.addOrUpdateProduct`. There is no on-chain check that rejects tokens with elastic supply. Rebasable tokens (e.g., AMPL, rebasing LSTs) are a well-known token class. Once such a token is listed, the vulnerability is triggered by the token's own rebase mechanism — no attacker action is required beyond submitting a normal withdrawal after the rebase. Any depositor of the affected token is an unprivileged caller who can trigger the impact.

---

### Recommendation

1. **Preferred — disallow rebasable tokens at listing time**: Add a check in `addOrUpdateProduct` that rejects tokens whose `totalSupply` can change without a transfer (e.g., require a snapshot or use a wrapper).
2. **Alternative — extend `assertUtilization` to compare against real balance**:

```solidity
function assertUtilization(uint32 productId) external view {
    (State memory _state, ) = getStateAndBalance(productId, X_ACCOUNT);
    int128 totalDeposits = _state.totalDepositsNormalized.mul(
        _state.cumulativeDepositsMultiplierX18
    );
    int128 totalBorrows = _state.totalBorrowsNormalized.mul(
        _state.cumulativeBorrowsMultiplierX18
    );
    require(totalDeposits >= totalBorrows, ERR_MAX_UTILIZATION);

    // NEW: ensure real balance covers net deposits
    address tokenAddr = configs[productId].token;
    uint128 realBalance = uint128(IERC20Base(tokenAddr).balanceOf(clearinghouse));
    int256 multiplier = int256(10**(MAX_DECIMALS - IERC20Base(tokenAddr).decimals()));
    int128 realBalanceX18 = int128(uint128(realBalance)) * int128(multiplier);
    require(realBalanceX18 >= totalDeposits - totalBorrows, ERR_MAX_UTILIZATION);
}
```

---

### Proof of Concept

1. Owner calls `SpotEngine.addOrUpdateProduct` listing AMPL (a rebasable token) as `productId = 5`.
2. Alice deposits 1 000 AMPL via `Endpoint.depositCollateral` → `Clearinghouse` holds 1 000 AMPL; ledger `totalDeposits = 1 000`.
3. Bob deposits 1 000 AMPL → `Clearinghouse` holds 2 000 AMPL; ledger `totalDeposits = 2 000`.
4. AMPL negative rebase (−20%) fires: `Clearinghouse.balanceOf(AMPL)` drops to **1 600** with no on-chain event to the protocol. Ledger still reads `totalDeposits = 2 000`.
5. Alice submits `WithdrawCollateral(amount=1000)` via the sequencer. `withdrawCollateral` executes:
   - `handleWithdrawTransfer` → `safeTransfer(withdrawPool, 1000)` succeeds (1 600 → 600 remaining).
   - `assertUtilization`: `2000 >= 0` ✓ (no borrows).
   - Alice receives 1 000 AMPL — her full pre-rebase ledger amount.
6. Bob submits `WithdrawCollateral(amount=1000)`:
   - `handleWithdrawTransfer` → `safeTransfer(withdrawPool, 1000)` **reverts** (only 600 AMPL remain).
   - Bob is permanently unable to withdraw his full share; 400 AMPL of his deposit is unrecoverable. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** core/contracts/Clearinghouse.sol (L377-389)
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

    function _balanceOf(address token) internal view virtual returns (uint128) {
        return uint128(IERC20Base(token).balanceOf(address(this)));
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
