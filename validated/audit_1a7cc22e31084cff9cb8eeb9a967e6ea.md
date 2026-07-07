### Title
Residual Allowance from `uint128` Truncation in `creditDeposit` Permanently Bricks USDT Deposits for a Subaccount DDA — (File: `core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` approves the endpoint for a `uint256 balance` but passes `uint128(balance)` as the deposit amount. When `balance > type(uint128).max`, the endpoint only pulls `uint128(balance)` tokens, leaving a non-zero residual allowance. On the next call to `creditDeposit()` for a USDT-like token (which reverts on non-zero → non-zero `approve`), the approval step reverts, permanently bricking deposits for that subaccount's DDA.

---

### Finding Description

In `DirectDepositV1.creditDeposit()`, for each product token held by the contract:

```solidity
uint256 balance = token.balanceOf(address(this));
if (balance != 0) {
    token.approve(address(endpoint), balance);          // approves uint256 balance
    endpoint.depositCollateralWithReferral(
        subaccount,
        productId,
        uint128(balance),                               // deposits only uint128(balance)
        "-1"
    );
}
```

The approved amount is `balance` (a `uint256`), but the amount actually transferred by the endpoint via `transferFrom` is `uint128(balance)`. When `balance > type(uint128).max`, the silent truncation means the endpoint consumes only `uint128(balance)` tokens, leaving `balance - uint128(balance)` as a non-zero residual allowance on the endpoint spender.

There is no allowance reset (`approve(endpoint, 0)`) before the new approval, unlike the correct pattern used elsewhere in the codebase in `ContractOwner.wrapVaultAsset()`:

```solidity
assetToken.approve(tokenAddr, 0);
assetToken.approve(tokenAddr, assetBalance);
```

On the next invocation of `creditDeposit()` for the same token, the call `token.approve(address(endpoint), newBalance)` will revert for USDT-like tokens (which enforce the non-zero → non-zero approval guard), permanently preventing any further deposits for that subaccount's DDA.

`creditDeposit()` is declared `external` with no access control, so any caller can trigger it.

---

### Impact Explanation

Once the residual allowance is set, every subsequent call to `creditDeposit()` for that token reverts at the `approve` step. Tokens sent to the DDA after this point cannot be deposited into the protocol. The subaccount's DDA is permanently bricked for that token. Funds accumulate in the DDA contract with no path to deposit them into the clearinghouse, effectively locking user collateral.

---

### Likelihood Explanation

The trigger requires `balance > type(uint128).max`. For USDT (6 decimals) this is ~3.4 × 10³² USDT — unrealistic in practice. For 18-decimal tokens it is ~3.4 × 10²⁰ tokens, still very large. Likelihood is **low** for the truncation path specifically. However, the structural absence of an allowance reset is a latent defect: any future code path that leaves a non-zero residual allowance (e.g., a partial-fill scenario, a deposit cap, or a minimum-deposit revert that is caught upstream) would trigger the same permanent bricking for USDT.

---

### Recommendation

Reset the allowance to zero before setting a new one, mirroring the pattern already used in `ContractOwner.wrapVaultAsset()`:

```solidity
token.approve(address(endpoint), 0);
token.approve(address(endpoint), balance);
```

Alternatively, add an `ERC20Helper.safeApprove` or `forceApprove` wrapper (analogous to OpenZeppelin's `forceApprove`) that unconditionally resets to zero before approving, and use it consistently across all approval sites.

---

### Proof of Concept

1. A large amount of a USDT-like token (balance > `type(uint128).max`) accumulates in a `DirectDepositV1` DDA.
2. Anyone calls `creditDeposit()`. The call `token.approve(endpoint, balance)` succeeds (allowance was 0). The endpoint's `depositCollateralWithReferral` pulls only `uint128(balance)` tokens, leaving `balance - uint128(balance)` as residual allowance.
3. More tokens arrive at the DDA.
4. Anyone calls `creditDeposit()` again. `token.approve(endpoint, newBalance)` reverts because USDT enforces non-zero → non-zero approval reversion.
5. All future `creditDeposit()` calls for this token revert. Tokens accumulate in the DDA with no deposit path. [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** core/contracts/ContractOwner.sol (L529-532)
```text
            IERC20Base assetToken = IERC20Base(assetTokenAddr);
            assetToken.approve(tokenAddr, 0);
            assetToken.approve(tokenAddr, assetBalance);
            IERC4626Base(tokenAddr).deposit(assetBalance, directDepositV1);
```
