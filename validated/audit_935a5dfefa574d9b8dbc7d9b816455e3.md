### Title
Missing Contract Existence Check in `ERC20Helper` Low-Level Calls Enables Phantom Deposit Crediting — (File: `core/contracts/libraries/ERC20Helper.sol`)

---

### Summary

`ERC20Helper.safeTransfer` and `safeTransferFrom` perform low-level `.call()` on a token address without verifying that the address contains deployed code. Per the EVM specification, a call to a codeless address returns `success = true` with empty return data. The library's success condition accepts `data.length == 0` as valid, so the call silently passes. If a registered token contract is self-destructed, a deposit can be queued and credited to a subaccount without any actual token transfer occurring.

---

### Finding Description

Both functions in `ERC20Helper` use the same pattern:

```solidity
(bool success, bytes memory data) = address(self).call(...);
require(
    success && (data.length == 0 || abi.decode(data, (bool))),
    ERR_TRANSFER_FAILED
);
``` [1](#0-0) [2](#0-1) 

When `self` has no deployed code (e.g., after `selfdestruct`), the EVM returns `(true, "")`. Because `data.length == 0` is true, the `require` passes without reverting. No tokens move.

This is consumed directly by `handleDepositTransfer` in `EndpointStorage.sol`:

```solidity
function handleDepositTransfer(IERC20Base token, address from, uint256 amount) internal {
    require(address(token) != address(0), ERR_INVALID_PRODUCT);
    safeTransferFrom(token, from, amount);
    safeTransferTo(token, address(clearinghouse), amount);
}
``` [3](#0-2) 

The only guard is a zero-address check. There is no `address(token).code.length > 0` check. A non-zero address with no code passes through both `safeTransferFrom` and `safeTransferTo` silently.

The same absence of a code existence check applies to `handleWithdrawTransfer` in `BaseWithdrawPool.sol`, which calls `token.safeTransfer(to, uint256(amount))` with no code guard: [4](#0-3) 

---

### Impact Explanation

**Deposit path (primary impact — collateral theft):**

A user deposits via `Endpoint.depositCollateral` → `handleDepositTransfer`. If the token has no code, both the `safeTransferFrom` (user → Endpoint) and `safeTransferTo` (Endpoint → Clearinghouse) calls silently succeed. The deposit is queued in `slowModeTxs`. [5](#0-4) 

When the sequencer processes the slow-mode transaction, `Clearinghouse.depositCollateral` credits the subaccount's internal balance: [6](#0-5) 

The Clearinghouse's on-chain token balance does not increase, but the attacker's internal balance does. The attacker can then use this phantom collateral to borrow against or withdraw real assets from other products (subject to health checks), effectively stealing from the protocol's real reserves.

**Withdrawal path (secondary impact — loss of user funds):**

If a token is self-destructed after a withdrawal is queued, `handleWithdrawTransfer` silently succeeds, the user's internal balance is debited, but they receive nothing.

---

### Likelihood Explanation

This requires a registered token contract to be self-destructed. On chains where `selfdestruct` retains its full effect (pre-EIP-6780 semantics, or chains that have not adopted EIP-6780 — including many L2s where Nado is deployed), this is a realistic scenario. A token contract with an accessible `selfdestruct` path, or one that is upgradeable via a proxy that can be pointed to a destructing implementation, is sufficient. The attacker does not need any privileged access to the Nado protocol itself — only the ability to call `depositCollateral` with a productId whose token has been destroyed.

---

### Recommendation

Add a contract existence check inside `ERC20Helper.safeTransfer` and `ERC20Helper.safeTransferFrom` before the low-level call:

```solidity
require(address(self).code.length > 0, ERR_TRANSFER_FAILED);
```

Alternatively, add the check in `handleDepositTransfer` and `handleWithdrawTransfer` at the call sites. Document that registering a token whose contract can be self-destructed is a protocol invariant violation.

---

### Proof of Concept

1. Token contract for `productId = X` is registered in `SpotEngine` with a non-zero address `tokenAddr`.
2. `tokenAddr` is self-destructed (e.g., via an accessible `selfdestruct` path in the token or its proxy).
3. Attacker calls `Endpoint.depositCollateral(subaccountName, X, 1_000_000)`.
4. `handleDepositTransfer` is entered; `address(token) != address(0)` passes because `tokenAddr` is still non-zero.
5. `safeTransferFrom` executes `tokenAddr.call(abi.encodeWithSelector(transfer.selector, ...))` → EVM returns `(true, "")` (no code at address).
6. `success = true`, `data.length == 0` → `require` passes. No tokens transferred.
7. `safeTransferTo` repeats the same silent success.
8. Deposit is enqueued in `slowModeTxs`.
9. Sequencer calls `submitTransactionsChecked` including `ExecuteSlowMode`; `Clearinghouse.depositCollateral` credits `1_000_000` units to the attacker's subaccount.
10. Attacker's subaccount now has phantom collateral. They call `withdrawCollateral` for a different product (e.g., USDC) up to their health limit, draining real protocol reserves. [7](#0-6) [3](#0-2) [6](#0-5)

### Citations

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

**File:** core/contracts/libraries/ERC20Helper.sol (L29-41)
```text
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(
                IERC20Base.transferFrom.selector,
                from,
                to,
                amount
            )
        );

        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
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

**File:** core/contracts/Endpoint.sol (L103-121)
```text
    function depositCollateral(
        bytes12 subaccountName,
        uint32 productId,
        uint128 amount
    ) external {
        bytes32 subaccount = bytes32(
            abi.encodePacked(msg.sender, subaccountName)
        );
        require(
            isValidDepositAmount(subaccount, productId, amount),
            ERR_DEPOSIT_TOO_SMALL
        );
        depositCollateralWithReferral(
            subaccount,
            productId,
            amount,
            DEFAULT_REFERRAL_CODE
        );
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
