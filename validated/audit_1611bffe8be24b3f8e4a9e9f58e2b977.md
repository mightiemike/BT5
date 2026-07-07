### Title
Non-Standard Token `approve()` in `creditDeposit()` Can Permanently Block Deposits - (File: `core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` calls `token.approve(address(endpoint), balance)` directly without first resetting the allowance to zero. For non-standard tokens like USDT that revert when `approve()` is called with a non-zero value while the existing allowance is already non-zero, any residual allowance left after a prior deposit will permanently brick the deposit path for that subaccount.

---

### Finding Description

In `DirectDepositV1.creditDeposit()`, for each product token held by the contract, the code approves the endpoint for exactly `balance` and then calls `depositCollateralWithReferral`: [1](#0-0) 

The raw `IIERC20Base.approve` call at line 92 does not reset the allowance to zero before setting the new value. If `endpoint.depositCollateralWithReferral` does not consume exactly `balance` tokens — for example, due to a fee-on-transfer token reducing the actual transferred amount, a partial fill, or any edge case in the endpoint's processing — the remaining allowance will be non-zero. On the next invocation of `creditDeposit()`, the `token.approve(address(endpoint), balance)` call will revert for USDT-like tokens, because USDT's `approve()` implementation requires the current allowance to be zero before setting a new non-zero value.

Notably, `ContractOwner.wrapVaultAsset()` already applies the correct two-step pattern (`approve(tokenAddr, 0)` then `approve(tokenAddr, assetBalance)`) for the same class of token interaction, demonstrating awareness of the issue elsewhere in the codebase: [2](#0-1) 

The `creditDeposit()` function has no access control — it is `external` with no modifier — so any unprivileged caller can trigger it: [3](#0-2) 

`ContractOwner.creditDepositV1()` also routes through this same function: [4](#0-3) 

---

### Impact Explanation

If a residual non-zero allowance is left after a `creditDeposit()` call for a USDT-like token, every subsequent call to `creditDeposit()` for that `DirectDepositV1` instance will revert at the `approve` step. This permanently blocks collateral deposits for the affected subaccount through the `DirectDepositV1` mechanism. Funds already sitting in the `DirectDepositV1` contract cannot be credited to the subaccount via the normal deposit path.

---

### Likelihood Explanation

USDT is one of the most widely used collateral tokens in DeFi. The residual allowance condition can arise if `depositCollateralWithReferral` in the endpoint processes a fee-on-transfer token (where the actual transferred amount is less than `balance`), or if any partial-consumption edge case exists in the endpoint. The `uint128(balance)` cast at line 96 also silently truncates if `balance > type(uint128).max`, meaning the approved amount and the deposited amount can diverge, leaving a residual allowance. [5](#0-4) 

---

### Recommendation

Apply the same two-step reset pattern already used in `ContractOwner.wrapVaultAsset()`:

```solidity
function creditDeposit() external {
    uint32[] memory productIds = spotEngine.getProductIds();
    for (uint256 i = 0; i < productIds.length; i++) {
        uint32 productId = productIds[i];
        address tokenAddr = spotEngine.getToken(productId);
        require(tokenAddr != address(0), "Invalid productId.");
        IIERC20Base token = IIERC20Base(tokenAddr);
        uint256 balance = token.balanceOf(address(this));
        if (balance != 0) {
            // Reset allowance to 0 first for non-standard tokens like USDT
            token.approve(address(endpoint), 0);
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

Additionally, address the `uint128` truncation risk by capping `balance` to `type(uint128).max` before both the approval and the deposit call, ensuring the approved amount always matches the deposited amount exactly.

---

### Proof of Concept

1. A `DirectDepositV1` is deployed for a subaccount that uses USDT as collateral.
2. USDT is sent to the `DirectDepositV1` contract address.
3. `creditDeposit()` is called. `balance = 1000e6`. `token.approve(endpoint, 1000e6)` succeeds (allowance was 0). `depositCollateralWithReferral` is called but, due to fee-on-transfer behavior, only `999e6` is pulled — leaving `1e6` residual allowance.
4. More USDT arrives. `creditDeposit()` is called again. `balance = 500e6`. `token.approve(endpoint, 500e6)` **reverts** because USDT's `approve` requires current allowance == 0.
5. All subsequent `creditDeposit()` calls revert. The subaccount's deposit path is permanently bricked. [6](#0-5)

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

**File:** core/contracts/ContractOwner.sol (L529-532)
```text
            IERC20Base assetToken = IERC20Base(assetTokenAddr);
            assetToken.approve(tokenAddr, 0);
            assetToken.approve(tokenAddr, assetBalance);
            IERC4626Base(tokenAddr).deposit(assetBalance, directDepositV1);
```
