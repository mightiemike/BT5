### Title
`ERC20Helper.safeTransfer` / `safeTransferFrom` Permanently Reverts for Tokens Returning `false` (e.g., Tether Gold / XAUT) — (`File: core/contracts/libraries/ERC20Helper.sol`)

---

### Summary

Nado's `ERC20Helper` library implements the standard "safe transfer" guard that requires either no return data or a `true` boolean return. Tokens such as Tether Gold (XAUT) that return `false` on every `transfer` / `transferFrom` call satisfy neither branch, causing every deposit and withdrawal to unconditionally revert. Any such token listed as a supported spot product becomes permanently non-functional for all users.

---

### Finding Description

`ERC20Helper.safeTransfer` and `ERC20Helper.safeTransferFrom` both enforce:

```solidity
require(
    success && (data.length == 0 || abi.decode(data, (bool))),
    ERR_TRANSFER_FAILED
);
``` [1](#0-0) [2](#0-1) 

For a token like XAUT that returns `false` (not a revert, but a `false` boolean):

- `success` = `true` (low-level call does not revert)
- `data.length` = 32 (a bool is returned)
- `abi.decode(data, (bool))` = `false`

The condition evaluates to `true && (false || false)` = `false` → **always reverts**.

This library is the sole transfer primitive used across all critical flows:

**Deposit path** — `Endpoint.depositCollateral` / `depositCollateralWithReferral` → `EndpointStorage.handleDepositTransfer` → `safeTransferFrom` + `safeTransfer`: [3](#0-2) [4](#0-3) 

**Withdrawal path** — `Clearinghouse.withdrawCollateral` → `handleWithdrawTransfer` → `token.safeTransfer`: [5](#0-4) 

**Slow-mode fee path** — `EndpointStorage.chargeSlowModeFee` → `token.safeTransferFrom`: [6](#0-5) 

`DirectDepositV1` contains an identical inline `safeTransfer` with the same guard: [7](#0-6) 

---

### Impact Explanation

Any spot product whose underlying token returns `false` on transfer (XAUT being the canonical real-world example) is rendered completely non-functional: every deposit call reverts, every withdrawal call reverts, and every slow-mode fee charge reverts. The protocol lists the product as supported but no user can interact with it. Because the revert happens before any state is written, no funds are locked — but the product is permanently dead for all users without an upgrade.

---

### Likelihood Explanation

Likelihood is low-to-medium. It requires the protocol to list a `false`-returning token as a supported product. XAUT is a real, liquid, widely-known token. If the protocol expands its supported asset list to include gold-backed or similarly non-standard ERC-20s, the failure is immediate and total. No attacker action is required — the first legitimate deposit attempt triggers the revert.

---

### Recommendation

Replace the strict boolean check with a pattern that also accepts a `false` return by treating it as a failed-but-non-reverting transfer and handling it explicitly, or use a try/catch wrapper. Alternatively, document that only tokens returning `true` or no data are supported, and enforce this at product-listing time via an on-chain transfer probe (send 0 tokens and verify the call succeeds).

The minimal fix in `ERC20Helper`:

```solidity
// Instead of reverting on false, check actual balance delta
uint256 before = IERC20Base(self).balanceOf(to);
address(self).call(...);
require(IERC20Base(self).balanceOf(to) - before == amount, ERR_TRANSFER_FAILED);
```

---

### Proof of Concept

1. Admin lists XAUT (Tether Gold) as a supported spot product via the normal product-registration flow.
2. User calls `Endpoint.depositCollateral(subaccountName, xautProductId, amount)`.
3. Execution reaches `EndpointStorage.handleDepositTransfer` → `ERC20Helper.safeTransferFrom`.
4. XAUT's `transferFrom` returns `false` (does not revert).
5. `ERC20Helper` evaluates `true && (false || false)` → `require` fails → transaction reverts with `ERR_TRANSFER_FAILED`.
6. Every subsequent deposit, withdrawal, and slow-mode fee charge for XAUT reverts identically.
7. The product is listed but permanently inaccessible to all users. [8](#0-7)

### Citations

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

**File:** core/contracts/EndpointStorage.sol (L83-93)
```text
    function chargeSlowModeFee(IERC20Base token, address from)
        internal
        virtual
    {
        require(address(token) != address(0));
        token.safeTransferFrom(
            from,
            address(this),
            clearinghouse.getSlowModeFee()
        );
    }
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

**File:** core/contracts/Endpoint.sol (L144-148)
```text
        handleDepositTransfer(
            IERC20Base(spotEngine.getToken(productId)),
            msg.sender,
            uint256(amount)
        );
```

**File:** core/contracts/Clearinghouse.sol (L377-385)
```text
    function handleWithdrawTransfer(
        IERC20Base token,
        address to,
        uint128 amount,
        uint64 idx
    ) internal virtual {
        token.safeTransfer(withdrawPool, uint256(amount));
        BaseWithdrawPool(withdrawPool).submitWithdrawal(token, to, amount, idx);
    }
```

**File:** core/contracts/DirectDepositV1.sol (L74-80)
```text
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(IIERC20Base.transfer.selector, to, amount)
        );
        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            "Transfer failed"
        );
```
