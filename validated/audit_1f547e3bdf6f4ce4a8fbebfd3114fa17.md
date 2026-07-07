### Title
Unsafe `approve()` in `DirectDepositV1.creditDeposit()` Permanently Breaks Deposits for USDT-like Tokens — (`File: core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` calls raw `token.approve(address(endpoint), balance)` without first resetting the allowance to zero. For ERC20 tokens that require the allowance to be zero before a new approval (e.g., USDT), any scenario that leaves a residual allowance will cause all subsequent `creditDeposit()` calls to permanently revert, trapping tokens in the DDA contract and preventing them from being credited to the subaccount.

---

### Finding Description

`DirectDepositV1.creditDeposit()` iterates over all spot product IDs, and for each token with a non-zero balance, it calls:

```solidity
token.approve(address(endpoint), balance);
endpoint.depositCollateralWithReferral(subaccount, productId, uint128(balance), "-1");
``` [1](#0-0) 

The `approve` call uses the raw `IIERC20Base.approve()` interface — no `SafeERC20.forceApprove()`, no prior reset to zero, and no `safeApprove` wrapper exists anywhere in the codebase (`ERC20Helper` only implements `safeTransfer` and `safeTransferFrom`). [2](#0-1) 

The concrete residual-allowance trigger: `depositCollateralWithReferral` accepts `uint128 amount`, but the DDA passes `uint128(balance)` where `balance` is `uint256`. If `balance > type(uint128).max`, the cast silently truncates — the endpoint pulls only `uint128(balance)` tokens, leaving a residual allowance of `balance - uint128(balance)` on the endpoint. On the next call to `creditDeposit()`, `token.approve(address(endpoint), newBalance)` is invoked with a non-zero existing allowance, which causes USDT (and any token with the same guard) to revert unconditionally. [3](#0-2) 

Notably, `ContractOwner.wrapVaultAsset()` — a parallel flow in the same codebase — correctly resets the allowance to zero before re-approving, demonstrating that the protocol is aware of this pattern but did not apply it in `creditDeposit()`:

```solidity
assetToken.approve(tokenAddr, 0);
assetToken.approve(tokenAddr, assetBalance);
``` [4](#0-3) 

---

### Impact Explanation

Once a residual allowance exists for a USDT-like token, every subsequent call to `creditDeposit()` for that token reverts. Tokens sent to the DDA accumulate there and cannot be credited to the subaccount via the normal deposit flow. The deposit mechanism for that token in that DDA is permanently broken. While the owner can recover tokens via `ContractOwner.withdrawFromDirectDepositV1()`, this defeats the purpose of the DDA and requires manual owner intervention for every deposit. [5](#0-4) 

---

### Likelihood Explanation

`creditDepositV1()` on `ContractOwner` is `external` with no access control — any unprivileged caller can trigger it. [6](#0-5) 

`DirectDepositV1.creditDeposit()` is also `external` with no access control. [7](#0-6) 

The `uint128` overflow trigger requires an extremely large balance, making it unlikely in practice. However, the structural absence of `forceApprove` means any future code path or edge case that leaves a residual allowance (e.g., a minimum-deposit guard in the endpoint that succeeds without calling `transferFrom`) would trigger the same permanent revert. The likelihood is **low for the overflow trigger specifically**, but the missing safety pattern is a latent risk for any USDT-like token listed as a spot product.

---

### Recommendation

Replace the raw `approve` call in `DirectDepositV1.creditDeposit()` with a two-step reset pattern or use OpenZeppelin's `SafeERC20.forceApprove()`:

```solidity
// Option 1: two-step reset
token.approve(address(endpoint), 0);
token.approve(address(endpoint), balance);

// Option 2: use forceApprove (preferred)
SafeERC20.forceApprove(IERC20(address(token)), address(endpoint), balance);
```

Also update the `IIERC20Base` interface or switch to OpenZeppelin's `IERC20` + `SafeERC20` to ensure consistent safe-approval semantics across all token interactions. [8](#0-7) 

---

### Proof of Concept

1. A spot product is listed with a USDT-like token (requires allowance == 0 before `approve`).
2. A large amount of that token (> `type(uint128).max`) is sent to the DDA.
3. Anyone calls `ContractOwner.creditDepositV1(subaccount)`.
4. `creditDeposit()` calls `token.approve(endpoint, balance)` — succeeds (allowance was 0).
5. `depositCollateralWithReferral(..., uint128(balance), ...)` is called — endpoint pulls only `uint128(balance)` tokens; residual allowance = `balance - uint128(balance)` remains.
6. Any subsequent call to `creditDepositV1(subaccount)` for that token calls `token.approve(endpoint, newBalance)` with non-zero existing allowance → USDT reverts unconditionally.
7. All future deposits of that USDT-like token to that DDA are permanently stuck. [7](#0-6)

### Citations

**File:** core/contracts/DirectDepositV1.sol (L6-12)
```text
interface IIERC20Base {
    function transfer(address to, uint256 amount) external returns (bool);

    function balanceOf(address account) external view returns (uint256);

    function approve(address spender, uint256 amount) external returns (bool);
}
```

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

**File:** core/contracts/libraries/ERC20Helper.sol (L1-42)
```text
// SPDX-License-Identifier: GPL-2.0-or-later
pragma solidity ^0.8.0;

import "../interfaces/IERC20Base.sol";
import "../common/Errors.sol";

// @dev Adapted from https://github.com/Uniswap/v3-core/blob/main/contracts/libraries/TransferHelper.sol
library ERC20Helper {
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

    function safeTransferFrom(
        IERC20Base self,
        address from,
        address to,
        uint256 amount
    ) internal {
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
