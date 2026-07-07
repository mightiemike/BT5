### Title
Unchecked Raw `approve` Without Allowance Reset in `DirectDepositV1.creditDeposit()` Permanently Bricks DDA Deposits for Non-Standard ERC20 Tokens — (File: `core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` calls `token.approve(address(endpoint), balance)` using the raw `IIERC20Base.approve` interface without (a) checking the return value and (b) resetting the allowance to zero before setting a new value. The codebase's `ERC20Helper` library provides `safeTransfer` and `safeTransferFrom` but has no `safeApprove` equivalent. A residual non-zero allowance — reachable via the `uint256`→`uint128` truncation in the same function — causes USDT-like tokens to revert on the next `approve` call, permanently bricking the DDA for all listed tokens.

---

### Finding Description

`creditDeposit()` is a permissionless external function that iterates over every spot product, reads the DDA's token balance, approves the endpoint for that balance, and calls `depositCollateralWithReferral`:

```solidity
// DirectDepositV1.sol line 92-98
token.approve(address(endpoint), balance);          // balance is uint256
endpoint.depositCollateralWithReferral(
    subaccount,
    productId,
    uint128(balance),                               // silently truncated
    "-1"
);
```

Two compounding defects exist:

**Defect 1 — Unchecked return value.** `IIERC20Base.approve` is declared to return `bool`, but the return value is never inspected. For non-standard ERC20 tokens that signal failure by returning `false` rather than reverting, the approval silently fails. The subsequent `depositCollateralWithReferral` call then executes without a valid allowance, causing the endpoint's `transferFrom` to fail or silently no-op, leaving user funds stranded in the DDA.

**Defect 2 — No allowance reset before re-approval.** The approve is set to `balance` (uint256), but the deposit amount passed to the endpoint is `uint128(balance)`. If `balance > type(uint128).max`, the endpoint only pulls `uint128(balance)` tokens, leaving a residual allowance of `balance − uint128(balance)` on the DDA. On the next invocation of `creditDeposit()`, the call `token.approve(address(endpoint), newBalance)` reverts for USDT-like tokens (which require the allowance to be zero before a new non-zero value is set). Because the loop contains no `try/catch`, this revert propagates and blocks deposits for every token in the same call, permanently suspending the DDA.

The `ERC20Helper` library — which the codebase uses for safe transfers — provides no `safeApprove` wrapper, and `creditDeposit()` does not use it at all. [1](#0-0) [2](#0-1) 

---

### Impact Explanation

- A USDT-like token listed as a spot product whose DDA has accumulated a residual non-zero allowance (via the `uint128` truncation path) will cause every subsequent `creditDeposit()` call to revert.
- Because the loop is monolithic with no per-token error isolation, a single reverting token blocks deposits for **all** tokens held by that DDA.
- User funds sent to the DDA are permanently undepositable via `creditDeposit()`. The only recovery path is `withdrawFromDirectDepositV1`, which is `onlyOwner` — meaning the user cannot self-rescue.
- This is a direct analog to the M-17 basket suspension: the DDA becomes permanently suspended and the user loses the ability to deposit collateral. [3](#0-2) [4](#0-3) 

---

### Likelihood Explanation

- The trigger requires a USDT-like token to be listed as a spot product **and** a non-zero residual allowance to exist on the DDA.
- The residual allowance arises when `balance > type(uint128).max`. While this is a large number, an attacker can deliberately send `type(uint128).max + 1` tokens of a USDT-like token to the DDA and call `creditDeposit()`. The `uint128` cast wraps to 0, the endpoint rejects a zero-amount deposit (reverting the transaction and restoring the allowance), **or** if the endpoint accepts it, the allowance of `type(uint128).max + 1` is left intact. On the next legitimate `creditDeposit()` call the USDT revert fires.
- `creditDeposit()` has no access control — any address can call it, including an attacker timing the call to exploit the residual allowance state.
- Likelihood: **Medium** — requires a USDT-like collateral token and a deliberate or accidental oversized balance, both realistic on EVM chains where USDT is a primary collateral asset. [3](#0-2) 

---

### Recommendation

Replace the raw `approve` call with a safe pattern that (1) resets allowance to zero first and (2) checks the return value, mirroring the existing `ERC20Helper.safeTransfer` pattern:

```solidity
function safeApprove(IIERC20Base token, address spender, uint256 amount) internal {
    // Reset to 0 first for USDT-like tokens
    (bool s1, bytes memory d1) = address(token).call(
        abi.encodeWithSelector(IIERC20Base.approve.selector, spender, 0)
    );
    require(s1 && (d1.length == 0 || abi.decode(d1, (bool))), "Approve reset failed");

    (bool s2, bytes memory d2) = address(token).call(
        abi.encodeWithSelector(IIERC20Base.approve.selector, spender, amount)
    );
    require(s2 && (d2.length == 0 || abi.decode(d2, (bool))), "Approve failed");
}
```

Apply this in `creditDeposit()`:

```solidity
if (balance != 0) {
    safeApprove(token, address(endpoint), uint128(balance)); // match deposit amount
    endpoint.depositCollateralWithReferral(subaccount, productId, uint128(balance), "-1");
}
```

Note: the approve amount should also be `uint128(balance)` (not `balance`) to match the actual deposit amount and avoid leaving residual allowance. [5](#0-4) [6](#0-5) 

---

### Proof of Concept

```
Setup:
  - USDT (non-standard ERC20, reverts on non-zero allowance re-approval) is listed as a spot product
  - Attacker sends (type(uint128).max + 1) USDT to the DDA

Step 1: Attacker calls creditDeposit()
  - balance = type(uint128).max + 1
  - token.approve(endpoint, type(uint128).max + 1)  → allowance = type(uint128).max + 1
  - depositCollateralWithReferral(..., uint128(type(uint128).max + 1)=0, ...)
    → endpoint rejects 0-amount deposit, tx reverts → allowance reset to 0
    (OR endpoint accepts 0-amount, allowance stays at type(uint128).max + 1)

Step 2 (if endpoint accepts 0-amount):
  - Allowance on DDA for endpoint = type(uint128).max + 1 (non-zero)
  - Victim sends 100 USDT to DDA and calls creditDeposit()
  - token.approve(endpoint, 100) → USDT REVERTS (allowance != 0)
  - Entire loop reverts → all tokens in DDA are undepositable
  - Victim's 100 USDT is permanently stuck (only owner can rescue via withdrawFromDirectDepositV1)
``` [1](#0-0)

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

**File:** core/contracts/libraries/ERC20Helper.sol (L8-42)
```text
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
