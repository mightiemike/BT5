### Title
Residual Non-Zero Allowance in `creditDeposit` Blocks Subsequent Deposits for USDT-Like Tokens — (`File: core/contracts/DirectDepositV1.sol`)

### Summary

`DirectDepositV1.creditDeposit()` calls `token.approve(address(endpoint), balance)` without first resetting the allowance to zero. `Endpoint.depositCollateralWithReferral` contains a silent early-return path that skips the `transferFrom` when the deposit amount is below the minimum threshold. When this silent return fires, the approved allowance is never consumed, leaving a non-zero residual. For tokens with USDT-style non-standard `approve` semantics (which revert when changing a non-zero allowance to another non-zero value), every subsequent `creditDeposit()` call for that token will revert at the `approve` step, permanently blocking deposits until admin recovery.

### Finding Description

`DirectDepositV1.creditDeposit()` iterates over all spot product IDs and, for each token with a non-zero balance, calls:

```solidity
token.approve(address(endpoint), balance);
endpoint.depositCollateralWithReferral(subaccount, productId, uint128(balance), "-1");
``` [1](#0-0) 

Inside `Endpoint.depositCollateralWithReferral`, there is an explicit silent-return guard:

```solidity
if (!isValidDepositAmount(subaccount, productId, amount)) {
    return;
}
``` [2](#0-1) 

When this guard fires (deposit amount below minimum), `handleDepositTransfer` is never reached, so no `transferFrom` is executed and the allowance set by `token.approve(address(endpoint), balance)` is never consumed. The allowance remains at `balance` (non-zero).

On the next call to `creditDeposit()`, the same token still has a non-zero balance (tokens were not pulled), so `token.approve(address(endpoint), balance)` is called again with a non-zero value while the existing allowance is already non-zero. For USDT and other tokens that enforce the "approve from non-zero to non-zero" restriction, this call reverts, permanently blocking all future `creditDeposit()` calls for that token.

By contrast, `ContractOwner.wrapVaultAsset` correctly resets the allowance to zero before setting a new value:

```solidity
assetToken.approve(tokenAddr, 0);
assetToken.approve(tokenAddr, assetBalance);
``` [3](#0-2) 

`creditDeposit()` has no access control and is callable by any address. [4](#0-3) 

### Impact Explanation

For any USDT-like token supported as a spot product, once a sub-minimum deposit attempt leaves a residual allowance, all subsequent `creditDeposit()` calls for that token revert. The tokens are effectively locked inside the `DirectDepositV1` contract until the multisig owner calls `ContractOwner.withdrawFromDirectDepositV1`, which requires privileged admin intervention and disrupts the normal deposit flow for the affected subaccount. [5](#0-4) 

### Likelihood Explanation

The trigger condition — a token balance below the minimum deposit threshold — is realistic and reachable by any unprivileged actor who sends a small dust amount of a USDT-like token to the `DirectDepositV1` address before `creditDeposit()` is called. `creditDeposit()` is permissionless, so any user can call it. The silent-return path in `depositCollateralWithReferral` is explicitly designed to not revert (see the comment at line 138–140), making this a stable, reproducible trigger. [2](#0-1) 

### Recommendation

Reset the allowance to zero before setting a new non-zero allowance in `creditDeposit()`:

```solidity
token.approve(address(endpoint), 0);
token.approve(address(endpoint), balance);
```

Alternatively, use a `forceApprove`-style helper (as recommended in the referenced Smilee fix) that unconditionally sets the allowance regardless of the current value.

### Proof of Concept

1. A `DirectDepositV1` is deployed for subaccount `S` via `ContractOwner.createDirectDepositV1(S)`.
2. An attacker sends 1 wei of USDT to the `DirectDepositV1` address (below `MIN_DEPOSIT_AMOUNT`).
3. Anyone calls `creditDeposit()`. The loop reaches the USDT product:
   - `balance = 1`
   - `token.approve(endpoint, 1)` — allowance is now 1
   - `depositCollateralWithReferral(S, usdtProductId, 1, "-1")` — `isValidDepositAmount` returns false, function returns silently, no `transferFrom` executed
   - Allowance remains at 1
4. Later, a legitimate user sends 1000 USDT to the same `DirectDepositV1` address.
5. Anyone calls `creditDeposit()` again. The loop reaches USDT:
   - `balance = 1001`
   - `token.approve(endpoint, 1001)` — **REVERTS** because USDT's `approve` disallows changing a non-zero allowance to another non-zero value
6. The 1000 USDT is permanently stuck in the contract until admin recovery via `ContractOwner.withdrawFromDirectDepositV1`. [4](#0-3) [6](#0-5)

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

**File:** core/contracts/Endpoint.sol (L123-148)
```text
    function depositCollateralWithReferral(
        bytes32 subaccount,
        uint32 productId,
        uint128 amount,
        string memory
    ) public {
        require(!RiskHelper.isIsolatedSubaccount(subaccount), ERR_UNAUTHORIZED);

        address sender = address(bytes20(subaccount));

        // depositor / depositee need to be unsanctioned
        requireUnsanctioned(msg.sender);
        requireUnsanctioned(sender);

        if (!isValidDepositAmount(subaccount, productId, amount)) {
            // we cannot revert here, otherwise direct deposit could be blocked when there are
            // multiple assets awaiting credit but one of them is below the minimum deposit amount.
            // we can just skip the deposit and continue with the next asset.
            return;
        }

        handleDepositTransfer(
            IERC20Base(spotEngine.getToken(productId)),
            msg.sender,
            uint256(amount)
        );
```

**File:** core/contracts/ContractOwner.sol (L530-531)
```text
            assetToken.approve(tokenAddr, 0);
            assetToken.approve(tokenAddr, assetBalance);
```

**File:** core/contracts/ContractOwner.sol (L622-647)
```text
    function withdrawFromDirectDepositV1(bytes32 subaccount, address token)
        external
        onlyOwner
    {
        address payable directDepositV1 = directDepositV1Address[subaccount];
        require(directDepositV1 != address(0), "no dda");
        if (token == address(0)) {
            uint256 preBalance = address(this).balance;
            DirectDepositV1(directDepositV1).withdrawNative();
            uint256 postBalance = address(this).balance;
            require(postBalance > preBalance, "empty");
            (bool success, ) = msg.sender.call{value: postBalance - preBalance}(
                ""
            );
            require(success, "xfer");
        } else {
            uint256 preBalance = IERC20Base(token).balanceOf(address(this));
            DirectDepositV1(directDepositV1).withdraw(IIERC20Base(token));
            uint256 postBalance = IERC20Base(token).balanceOf(address(this));
            require(postBalance > preBalance, "empty");
            IERC20Base(token).safeTransfer(
                msg.sender,
                postBalance - preBalance
            );
        }
    }
```
