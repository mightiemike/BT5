### Title
Non-Standard Token `approve` Reverts in `DirectDepositV1.creditDeposit`, Permanently Blocking DDA Deposits — (`core/contracts/DirectDepositV1.sol`)

### Summary

`DirectDepositV1.creditDeposit()` calls `token.approve(address(endpoint), balance)` using the standard ABI-typed interface. For non-standard ERC20 tokens like USDT that return no value from `approve()`, the Solidity ABI decoder reverts when it tries to decode the expected `bool` return. The same function carefully uses a low-level `call`-based `safeTransfer` for the transfer step, but omits the same protection for `approve`, making the entire DDA deposit flow permanently broken for any such token.

### Finding Description

`DirectDepositV1` is a Direct Deposit Address contract. Its `creditDeposit()` function iterates over all spot product tokens, checks the DDA's balance, and for each non-zero balance calls `approve` then `depositCollateralWithReferral` on the endpoint. [1](#0-0) 

The `approve` call on line 92 goes through the typed `IIERC20Base` interface: [2](#0-1) 

`IIERC20Base.approve` is declared to return `bool`. When Solidity calls a function declared to return a value, the ABI decoder unconditionally attempts to decode the return data. USDT (and similar tokens) return no data from `approve`. The decoder finds zero bytes where it expects 32, and the call reverts.

By contrast, the `safeTransfer` helper defined in the same file explicitly uses a low-level `call` and checks `data.length == 0 || abi.decode(data, (bool))` to tolerate missing return data: [3](#0-2) 

No equivalent safe-approve wrapper exists anywhere in the codebase. `ERC20Helper` only provides `safeTransfer` and `safeTransferFrom`: [4](#0-3) 

### Impact Explanation

Any token listed as a spot product whose `approve` does not return a `bool` (USDT being the canonical example) will cause every call to `creditDeposit()` to revert. Tokens sent to the DDA address for that product are permanently stuck — they cannot be forwarded to the endpoint as collateral. The `withdraw` owner-only escape hatch uses the same `safeTransfer` low-level path and would still work, but the primary deposit flow is completely broken for affected tokens.

### Likelihood Explanation

`creditDeposit()` has no access control — it is `external` with no modifier: [5](#0-4) 

It is also called by `ContractOwner.creditDepositV1()`: [6](#0-5) 

Any user or bot that sends a non-standard token to a DDA and then calls `creditDeposit()` (or triggers `creditDepositV1`) will hit the revert. The failure is deterministic and repeatable for every affected token.

### Recommendation

Replace the direct `token.approve(...)` call with a low-level safe-approve pattern analogous to the existing `safeTransfer` helper, or add a `safeApprove` function to `ERC20Helper.sol` using the same `address(self).call(abi.encodeWithSelector(...))` pattern with `data.length == 0 || abi.decode(data, (bool))` check. Apply it at line 92 of `DirectDepositV1.sol`.

### Proof of Concept

1. A USDT spot product is listed in `SpotEngine`.
2. A user sends USDT to the DDA address for their subaccount.
3. Anyone calls `DirectDepositV1.creditDeposit()` (or `ContractOwner.creditDepositV1(subaccount)`).
4. The loop reaches the USDT product; `token.balanceOf(address(this))` returns a non-zero `balance`.
5. `token.approve(address(endpoint), balance)` is executed. USDT's `approve` returns no data.
6. Solidity's ABI decoder attempts to decode a `bool` from empty return data and reverts with a decoding error.
7. The entire `creditDeposit` call reverts. The USDT remains stuck in the DDA. No collateral is deposited. [7](#0-6)

### Citations

**File:** core/contracts/DirectDepositV1.sol (L6-12)
```text
interface IIERC20Base {
    function transfer(address to, uint256 amount) external returns (bool);

    function balanceOf(address account) external view returns (uint256);

    function approve(address spender, uint256 amount) external returns (bool);
}
```

**File:** core/contracts/DirectDepositV1.sol (L69-81)
```text
    function safeTransfer(
        IIERC20Base self,
        address to,
        uint256 amount
    ) internal {
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(IIERC20Base.transfer.selector, to, amount)
        );
        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            "Transfer failed"
        );
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
