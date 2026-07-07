### Title
Approval Race Protection Incompatibility Permanently Locks USDT Deposits in `DirectDepositV1` — (`File: core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` calls `token.approve(address(endpoint), balance)` directly without first resetting the allowance to zero. For tokens like USDT that enforce approval race protection (requiring allowance == 0 before a new `approve`), any residual allowance after a prior deposit cycle causes all subsequent `creditDeposit()` calls to revert permanently, locking deposited collateral in the contract.

---

### Finding Description

In `DirectDepositV1.creditDeposit()`, for each spot product token, the function reads the contract's current balance, approves the endpoint for that amount, and calls `depositCollateralWithReferral`: [1](#0-0) 

The approval at line 92 is a raw `token.approve(address(endpoint), balance)` with no prior reset to zero: [2](#0-1) 

A residual allowance arises when `balance` (a `uint256`) exceeds `type(uint128).max`. The deposit call passes `uint128(balance)`, which silently truncates the value, so the endpoint pulls only `uint128(balance)` tokens while the approval was for the full `uint256 balance`. The leftover allowance is `balance - uint128(balance)` — non-zero and persistent. [3](#0-2) 

On the next invocation of `creditDeposit()`, USDT's `approve` guard fires:

```
require(!((_value != 0) && (allowed[msg.sender][_spender] != 0)));
```

This causes a revert, and since `creditDeposit()` has no access control and no fallback path, the USDT balance held by the `DirectDepositV1` contract is permanently undepositable via this function.

The `ERC20Helper` library used elsewhere in the protocol provides only `safeTransfer` and `safeTransferFrom` — no `forceApprove` or zero-reset pattern: [4](#0-3) 

By contrast, `ContractOwner.wrapVaultAsset()` correctly resets to zero before re-approving, demonstrating the protocol is aware of this pattern in at least one place: [5](#0-4) 

This safe pattern was not applied in `DirectDepositV1`.

---

### Impact Explanation

USDT deposited to a `DirectDepositV1` contract becomes permanently stuck once a dust allowance accumulates. `creditDeposit()` will revert on every subsequent call for that token, preventing the collateral from ever being credited to the subaccount. The `withdraw()` function is `onlyOwner`, so ordinary users have no self-rescue path. The locked funds represent real user collateral that cannot enter the protocol.

---

### Likelihood Explanation

`creditDeposit()` is `external` with no access modifier: [6](#0-5) 

`ContractOwner.creditDepositV1()` is also `external` with no access modifier, callable by any address: [7](#0-6) 

USDT is a commonly supported collateral token on EVM chains. The truncation trigger (`balance > type(uint128).max`) requires a large deposit (~1.8 × 10²⁵ raw USDT units), but residual allowance can also arise from any partial consumption by the endpoint. The combination of a permissionless entry point, a widely used token, and a silent truncation path makes this a realistic, medium-to-high likelihood issue.

---

### Recommendation

Replace the bare `approve` in `creditDeposit()` with a zero-reset-then-approve pattern, consistent with what `wrapVaultAsset` already does:

```solidity
token.approve(address(endpoint), 0);
token.approve(address(endpoint), balance);
```

Or, import OpenZeppelin's `SafeERC20` and use `forceApprove(address(endpoint), balance)`, which handles this atomically. Apply the same fix to `ContractOwner.depositInsurance()` at line 254, which has the same bare `approve` pattern (though that function is `onlyOwner`). [8](#0-7) 

---

### Proof of Concept

1. USDT is a supported spot collateral token in the protocol.
2. A `DirectDepositV1` contract is deployed for a subaccount via `ContractOwner.createDirectDepositV1()`.
3. A user sends a USDT amount `B` where `B > type(uint128).max` to the `DirectDepositV1` address.
4. Anyone calls `ContractOwner.creditDepositV1(subaccount)` → `DirectDepositV1.creditDeposit()`.
5. `token.approve(endpoint, B)` succeeds (allowance = B). `depositCollateralWithReferral(..., uint128(B), ...)` pulls only `uint128(B)` tokens. Remaining allowance = `B - uint128(B)` > 0.
6. More USDT arrives at the `DirectDepositV1` address (e.g., a second user deposit).
7. Anyone calls `creditDepositV1(subaccount)` again. `token.approve(endpoint, newBalance)` reverts because USDT's guard sees `allowed != 0`.
8. All subsequent USDT in the contract is permanently undepositable. The subaccount never receives the collateral credit.

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

**File:** core/contracts/libraries/ERC20Helper.sol (L1-43)
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
}
```

**File:** core/contracts/ContractOwner.sol (L253-255)
```text

        quoteToken.approve(address(endpoint), uint256(amount));

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
