### Title
Unchecked `approve` Return Value in `creditDeposit` Silently Fails to Set Allowance, Blocking Subaccount Deposits — (File: `core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` calls `token.approve(address(endpoint), balance)` without checking the boolean return value. If `approve` returns `false` for any supported spot token, execution continues and `endpoint.depositCollateralWithReferral(...)` is called with zero allowance, causing the endpoint's internal `transferFrom` to fail or revert. User funds remain stranded in the DDA contract and the subaccount is never credited.

---

### Finding Description

In `DirectDepositV1.creditDeposit()`, for each spot product token with a non-zero balance, the contract calls:

```solidity
token.approve(address(endpoint), balance);
endpoint.depositCollateralWithReferral(subaccount, productId, uint128(balance), "-1");
``` [1](#0-0) 

The `IIERC20Base` interface declares `approve` as returning `bool`: [2](#0-1) 

The return value is silently discarded. If `approve` returns `false`, the allowance is not set, but `depositCollateralWithReferral` is called anyway. The endpoint will attempt a `transferFrom` against zero allowance, causing a revert or silent failure, and the subaccount receives no credit.

The `ERC20Helper` library provides `safeTransfer` and `safeTransferFrom` wrappers that validate return values: [3](#0-2) 

However, no `safeApprove` equivalent exists in `ERC20Helper`, and `creditDeposit` does not use the helper for its `approve` call.

`creditDeposit()` is `external` with no access control modifier, making it callable by any unprivileged user or contract: [4](#0-3) 

---

### Impact Explanation

If `approve` returns `false` for a token (non-standard ERC20 behavior), the deposit to the subaccount fails. Tokens sent to the DDA contract remain stuck there and are not credited to the subaccount. The user's collateral is effectively locked in the DDA until a successful `creditDeposit` call can be made — which may never succeed if the token consistently returns `false` from `approve`. This corrupts the expected subaccount balance state: the DDA holds tokens that should have been deposited as collateral.

---

### Likelihood Explanation

The Nado protocol supports multiple spot tokens via `spotEngine.getProductIds()`. Any token that returns `false` from `approve` instead of reverting (a known pattern in older or non-standard ERC20 implementations) triggers this path. The function is publicly callable, so any user interacting through the DDA deposit flow is exposed. The likelihood is medium: standard tokens like USDC revert on failure rather than returning `false`, but the protocol's multi-token design means future or alternative token listings increase exposure.

---

### Recommendation

Check the return value of `approve` and revert if it returns `false`. Add a `safeApprove` function to `ERC20Helper` analogous to the existing `safeTransfer`:

```solidity
function safeApprove(IERC20Base self, address spender, uint256 amount) internal {
    (bool success, bytes memory data) = address(self).call(
        abi.encodeWithSelector(IERC20Base.approve.selector, spender, amount)
    );
    require(
        success && (data.length == 0 || abi.decode(data, (bool))),
        ERR_APPROVE_FAILED
    );
}
```

Then replace the bare `token.approve(...)` call in `creditDeposit` with this safe wrapper.

---

### Proof of Concept

1. A spot token `T` is listed in `SpotEngine` that returns `false` from `approve` (non-reverting failure).
2. A user sends `N` units of `T` to the DDA contract for subaccount `S`.
3. Anyone calls `DirectDepositV1(dda).creditDeposit()`.
4. The loop reaches token `T`, finds `balance = N > 0`.
5. `token.approve(address(endpoint), N)` is called — returns `false`, return value discarded, allowance remains 0.
6. `endpoint.depositCollateralWithReferral(S, productId, N, "-1")` is called.
7. The endpoint attempts `transferFrom(dda, ..., N)` — fails due to zero allowance (reverts or returns false).
8. The entire `creditDeposit` transaction reverts (or the deposit is skipped), leaving `N` tokens stranded in the DDA.
9. Subaccount `S` receives no collateral credit despite the user having funded the DDA. [4](#0-3)

### Citations

**File:** core/contracts/DirectDepositV1.sol (L11-11)
```text
    function approve(address spender, uint256 amount) external returns (bool);
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

**File:** core/contracts/libraries/ERC20Helper.sol (L9-21)
```text
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
```
