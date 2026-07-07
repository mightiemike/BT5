### Title
Missing `approve(0)` Before Re-Approval Blocks USDT Deposits via DirectDepositV1 — (File: `core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` calls `token.approve(address(endpoint), balance)` without first resetting the allowance to zero. For USDT-like tokens that enforce a zero-allowance precondition on `approve()`, any call to `creditDeposit()` when a non-zero residual allowance already exists will revert, permanently blocking collateral deposits for that token through the DDA mechanism.

---

### Finding Description

In `DirectDepositV1.creditDeposit()`, for each supported spot product, the contract approves the endpoint for the full token balance and then calls `depositCollateralWithReferral`:

```solidity
// core/contracts/DirectDepositV1.sol, lines 91–98
if (balance != 0) {
    token.approve(address(endpoint), balance);
    endpoint.depositCollateralWithReferral(
        subaccount,
        productId,
        uint128(balance),
        "-1"
    );
}
```

The approval is set to `balance` (a `uint256`), but the deposit amount passed to the endpoint is `uint128(balance)`. If `balance > type(uint128).max`, the endpoint only consumes `uint128(balance)` tokens, leaving a residual allowance of `balance - uint128(balance)`. On the next invocation of `creditDeposit()`, the code attempts `token.approve(endpoint, newBalance)` on top of a non-zero allowance — which reverts for USDT.

Even within the `uint128` range, if the endpoint's `depositCollateralWithReferral` does not consume the exact approved amount (e.g., due to a minimum-deposit guard that silently skips the transfer, or a future upgrade), the residual allowance persists and the next `creditDeposit()` call reverts for USDT.

By contrast, `ContractOwner.wrapVaultAsset()` already applies the correct pattern:

```solidity
// core/contracts/ContractOwner.sol, lines 530–531
assetToken.approve(tokenAddr, 0);
assetToken.approve(tokenAddr, assetBalance);
```

`creditDeposit()` does not follow this safe pattern.

---

### Impact Explanation

If USDT (or any token with the same non-zero allowance guard) is listed as a supported spot collateral product, `creditDeposit()` will revert on any call where a non-zero residual allowance exists. This permanently blocks the DDA deposit path for that token and subaccount, locking any USDT balance held by the DDA contract until an admin manually resets state. The sixth-largest stablecoin pool would be unusable through the DDA mechanism.

---

### Likelihood Explanation

`ContractOwner.creditDepositV1()` has no access control — any caller can invoke it for any subaccount. The residual-allowance condition arises concretely when `balance > type(uint128).max` (approval is `uint256(balance)` but deposit is `uint128(balance)`), or if the endpoint does not consume the full approved amount. The `uint128` overflow path is unlikely for USDT (6 decimals), but the absence of `approve(0)` means the contract is one endpoint behavior change or one edge-case balance away from a permanent DoS on USDT deposits.

---

### Recommendation

Apply the same `approve(0)` → `approve(amount)` pattern already used in `wrapVaultAsset()`:

```solidity
token.approve(address(endpoint), 0);
token.approve(address(endpoint), balance);
```

Or import OpenZeppelin's `SafeERC20` and use `forceApprove(address(endpoint), balance)`, which handles the reset internally.

---

### Proof of Concept

1. USDT is listed as a supported spot collateral product in `SpotEngine`.
2. A DDA is created for subaccount `S` via `ContractOwner.createDirectDepositV1(S)`.
3. USDT is sent to the DDA. `creditDepositV1(S)` is called; `creditDeposit()` sets `approve(endpoint, X)` and calls `depositCollateralWithReferral(..., uint128(X), ...)`.
4. Due to any condition where the endpoint does not consume exactly `X` tokens (e.g., `X > type(uint128).max`), a residual allowance remains.
5. More USDT arrives at the DDA. Any caller invokes `creditDepositV1(S)` again.
6. `creditDeposit()` calls `token.approve(endpoint, newBalance)` with a non-zero existing allowance → USDT reverts → the entire `creditDeposit()` call reverts → USDT is permanently stuck in the DDA. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** core/contracts/DirectDepositV1.sol (L91-99)
```text
            if (balance != 0) {
                token.approve(address(endpoint), balance);
                endpoint.depositCollateralWithReferral(
                    subaccount,
                    productId,
                    uint128(balance),
                    "-1"
                );
            }
```

**File:** core/contracts/ContractOwner.sol (L502-508)
```text
    function creditDepositV1(bytes32 subaccount) external {
        address payable directDepositV1 = directDepositV1Address[subaccount];
        if (directDepositV1 == address(0)) {
            directDepositV1 = createDirectDepositV1(subaccount);
        }
        DirectDepositV1(directDepositV1).creditDeposit();
    }
```

**File:** core/contracts/ContractOwner.sol (L530-532)
```text
            assetToken.approve(tokenAddr, 0);
            assetToken.approve(tokenAddr, assetBalance);
            IERC4626Base(tokenAddr).deposit(assetBalance, directDepositV1);
```
