### Title
Unchecked ERC20 `approve()` Return Value in `creditDeposit()` Silently Fails to Authorize Endpoint Collateral Pull — (`File: core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` calls `token.approve(address(endpoint), balance)` without checking the boolean return value. For non-standard ERC20 tokens that return `false` on a failed approval instead of reverting, the approval silently fails. The subsequent `depositCollateralWithReferral` call then executes against an endpoint that has no allowance, causing the deposit to fail or be silently skipped — leaving user funds permanently stranded in the DDA contract without being credited to the target subaccount.

---

### Finding Description

In `DirectDepositV1.creditDeposit()`, for each spot product token held by the contract, the code calls:

```solidity
token.approve(address(endpoint), balance);
endpoint.depositCollateralWithReferral(subaccount, productId, uint128(balance), "-1");
``` [1](#0-0) 

The `IIERC20Base` interface declares `approve` as returning `bool`: [2](#0-1) 

But the return value is never inspected. The same file implements a `safeTransfer` helper that explicitly checks the return value via a low-level call: [3](#0-2) 

No equivalent `safeApprove` pattern exists. The `ERC20Helper` library used elsewhere in the protocol also only provides `safeTransfer` and `safeTransferFrom` — there is no `safeApprove`: [4](#0-3) 

---

### Impact Explanation

If a spot product token is non-standard and returns `false` on `approve` instead of reverting:

1. `token.approve(address(endpoint), balance)` executes, returns `false`, and the return value is discarded — no allowance is set.
2. `endpoint.depositCollateralWithReferral(...)` is called. The endpoint attempts `transferFrom` on the token with zero allowance.
3. Depending on the endpoint's transfer handling, the call either reverts (transaction fails silently from the user's perspective) or the deposit is skipped entirely.
4. User funds (real ERC20 tokens already held in the DDA contract) are never credited to the subaccount and become permanently stranded in `DirectDepositV1`.

The corrupted state delta is: **token balance remains in the DDA contract, subaccount balance is never incremented**, breaking the deposit accounting invariant.

---

### Likelihood Explanation

`creditDeposit()` has **no access control** — it is callable by any external address: [5](#0-4) 

The trigger requires a non-standard ERC20 token (returning `false` on failed approve) to be listed as a spot product. This is a realistic scenario given the protocol's design to support multiple collateral tokens. Any user or bot calling `creditDeposit()` on a DDA holding such a token would silently fail to deposit, with no on-chain error surfaced.

---

### Recommendation

Apply the same safe-call pattern already used in `safeTransfer` to the `approve` call, or add a `safeApprove` helper to `ERC20Helper`:

```solidity
function safeApprove(IERC20Base self, address spender, uint256 amount) internal {
    (bool success, bytes memory data) = address(self).call(
        abi.encodeWithSelector(IERC20Base.approve.selector, spender, amount)
    );
    require(
        success && (data.length == 0 || abi.decode(data, (bool))),
        ERR_TRANSFER_FAILED
    );
}
```

Then replace the bare `token.approve(address(endpoint), balance)` in `creditDeposit()` with `safeApprove(token, address(endpoint), balance)`.

---

### Proof of Concept

1. A non-standard ERC20 token `T` (returns `false` on approve, does not revert) is listed as a spot product in `SpotEngine`.
2. A user sends `N` units of `T` to a `DirectDepositV1` DDA contract targeting their subaccount.
3. Anyone calls `DirectDepositV1.creditDeposit()`.
4. The loop reaches product `T`: `token.approve(address(endpoint), N)` returns `false` — silently ignored.
5. `endpoint.depositCollateralWithReferral(subaccount, productId, N, "-1")` is called with zero allowance on the endpoint.
6. The endpoint's internal `transferFrom` fails (or is skipped), and `N` tokens remain in the DDA contract.
7. The subaccount balance is never credited. The user's funds are permanently stranded with no revert or event indicating failure. [5](#0-4)

### Citations

**File:** core/contracts/DirectDepositV1.sol (L11-11)
```text
    function approve(address spender, uint256 amount) external returns (bool);
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
