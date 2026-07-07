### Title
Missing `approve(..., 0)` Reset Before Re-approval in `DirectDepositV1.creditDeposit()` Causes Permanent Deposit Failure for USDT-like Tokens — (`core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` calls `token.approve(address(endpoint), balance)` directly without first resetting the allowance to zero. For tokens like USDT that revert when `approve` is called with a non-zero value while the existing allowance is also non-zero, any scenario that leaves a residual allowance will permanently brick the deposit path for that token in the DDA contract. The same codebase already applies the correct reset pattern in `ContractOwner.wrapVaultAsset()`, confirming developer awareness of the issue elsewhere.

---

### Finding Description

In `DirectDepositV1.creditDeposit()`, for each product token with a non-zero balance, the function sets an allowance and immediately calls `depositCollateralWithReferral`:

```solidity
token.approve(address(endpoint), balance);          // no prior approve(..., 0)
endpoint.depositCollateralWithReferral(
    subaccount,
    productId,
    uint128(balance),   // ← cast truncates if balance > type(uint128).max
    "-1"
);
``` [1](#0-0) 

The `approve` call is for the full `uint256 balance`, but the deposit call passes only `uint128(balance)`. If `balance > type(uint128).max`, the endpoint pulls only `uint128(balance)` tokens, leaving a non-zero residual allowance of `balance - uint128(balance)`. On any subsequent call to `creditDeposit()`, the line `token.approve(address(endpoint), balance)` will revert for USDT (and any token following the same non-zero-to-non-zero approval restriction), permanently blocking deposits through this DDA.

By contrast, `ContractOwner.wrapVaultAsset()` in the same codebase correctly applies the two-step reset pattern:

```solidity
assetToken.approve(tokenAddr, 0);
assetToken.approve(tokenAddr, assetBalance);
``` [2](#0-1) 

This inconsistency confirms the pattern is known and intentionally applied in some places but omitted in `DirectDepositV1`.

---

### Impact Explanation

Once a residual allowance exists for a USDT-like token in the DDA contract, every future call to `creditDeposit()` reverts at the `approve` step for that token. Tokens accumulating in the DDA contract can no longer be deposited into the protocol on behalf of the subaccount. The funds are not lost (they remain in the DDA), but the deposit mechanism is permanently broken until the allowance is manually cleared — which is not possible through any unprivileged path in `DirectDepositV1`. The only recovery path is `ContractOwner.withdrawFromDirectDepositV1()`, which is `onlyOwner`.

---

### Likelihood Explanation

The trigger condition — `balance > type(uint128).max` — is extreme for most tokens but is a realistic edge case for low-decimal tokens (e.g., USDT with 6 decimals: `type(uint128).max / 1e6 ≈ 3.4 × 10^32` USDT, which is unrealistic). However, a second realistic trigger exists: if the endpoint's `depositCollateralWithReferral` implementation caps or partially consumes the approved amount for any reason (e.g., a deposit ceiling, a paused state that consumes 0 tokens without reverting, or a future upgrade), the residual allowance scenario becomes immediately reachable. The function `creditDeposit()` has no access control and is callable by any address, including the subaccount owner or any third party. [3](#0-2) 

---

### Recommendation

Apply the same two-step reset pattern already used in `ContractOwner.wrapVaultAsset()`:

```solidity
token.approve(address(endpoint), 0);
token.approve(address(endpoint), balance);
endpoint.depositCollateralWithReferral(subaccount, productId, uint128(balance), "-1");
``` [4](#0-3) 

---

### Proof of Concept

1. A DDA is created for a subaccount via `ContractOwner.createDirectDepositV1(subaccount)`.
2. A USDT-like token (non-zero-to-non-zero `approve` reverts) is sent to the DDA in an amount exceeding `type(uint128).max` raw units (or the endpoint partially consumes the allowance for any reason).
3. `creditDeposit()` is called (by anyone — no access control). The `approve(endpoint, balance)` succeeds (first call, allowance was 0). The endpoint pulls only `uint128(balance)` tokens, leaving residual allowance `balance - uint128(balance) > 0`.
4. More tokens arrive at the DDA (or the same tokens remain if the deposit was partial).
5. `creditDeposit()` is called again. `token.approve(address(endpoint), balance)` reverts because USDT disallows changing a non-zero allowance to another non-zero value.
6. All future `creditDeposit()` calls for this token revert. Tokens accumulate in the DDA with no deposit path available to unprivileged callers. [3](#0-2) [5](#0-4)

### Citations

**File:** core/contracts/DirectDepositV1.sol (L83-101)
```text
    function creditDeposit() external {
        uint32[] memory productIds = spotEngine.getProductIds();
        for (uint256 i = 0; i < productIds.length; i++) {
            uint32 productId = productIds[i];
            address tokenAddr = spotEngine.getToken(productId);
            require(tokenAddr != address(0), "Invalid productId.");
            IIERC20Base token = IIERC20Base(tokenAddr);
            uint256 balance = token.balanceOf(address(this));
            if (balance != 0) {
                token.approve(address(endpoint), balance);
                endpoint.depositCollateralWithReferral(
                    subaccount,
                    productId,
                    uint128(balance),
                    "-1"
                );
            }
        }
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

**File:** core/contracts/ContractOwner.sol (L530-531)
```text
            assetToken.approve(tokenAddr, 0);
            assetToken.approve(tokenAddr, assetBalance);
```
