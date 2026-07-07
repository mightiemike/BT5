### Title
Unchecked `approve()` Return Value in `DirectDepositV1.creditDeposit()` Permanently Breaks Deposit Flow for Non-Standard ERC20 Tokens — (`core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` calls `token.approve(address(endpoint), balance)` without checking the return value. For non-standard ERC20 tokens (e.g., USDT) that return `false` instead of reverting on a failed approval, execution silently continues into `endpoint.depositCollateralWithReferral(...)`. The endpoint's `safeTransferFrom` then reverts due to zero allowance, causing the entire `creditDeposit()` call to revert. User funds deposited to the DDA for such tokens are permanently stuck in the DDA contract, inaccessible to the user without owner intervention.

---

### Finding Description

In `DirectDepositV1.creditDeposit()`, the `approve()` call at line 92 discards its `bool` return value:

```solidity
token.approve(address(endpoint), balance);   // ❌ return value ignored
endpoint.depositCollateralWithReferral(
    subaccount,
    productId,
    uint128(balance),
    "-1"
);
``` [1](#0-0) 

The `IIERC20Base` interface explicitly declares `approve` as returning `bool`: [2](#0-1) 

For non-standard ERC20 tokens that return `false` on failure (rather than reverting), the approval silently fails, leaving the allowance at `0`. The subsequent `depositCollateralWithReferral` call reaches `ERC20Helper.safeTransferFrom` inside the endpoint, which checks the return value of `transferFrom` and reverts with `ERR_TRANSFER_FAILED`: [3](#0-2) 

This causes the entire `creditDeposit()` transaction to revert. The DDA holds the user's tokens but can never forward them to the endpoint. The only recovery path is `withdraw()`, which is `onlyOwner`: [4](#0-3) 

Contrast this with the `safeTransfer` helper defined in the same file, which correctly checks the return value — but is never applied to `approve`: [5](#0-4) 

---

### Impact Explanation

Any user who sends a non-standard ERC20 token (e.g., USDT) to their DDA and triggers `creditDeposit()` will have their funds permanently stuck in the DDA. The `creditDeposit()` function will always revert for such tokens, making the deposit flow completely non-functional. The user has no self-service recovery path — only the contract owner can rescue funds via `withdrawFromDirectDepositV1()`. This constitutes a concrete asset lock with no user-side remedy. [6](#0-5) 

---

### Likelihood Explanation

`creditDeposit()` has no access control — it is `external` and callable by any address: [7](#0-6) 

`ContractOwner.creditDepositV1()` also calls it on behalf of users: [8](#0-7) 

USDT is one of the most widely used stablecoins and is a known non-standard ERC20 token that returns `false` on failed approvals. If Nado supports USDT as a collateral token (a common configuration for a trading protocol), any user depositing USDT to their DDA will trigger this bug on every `creditDeposit()` call.

---

### Recommendation

Apply the same safe-call pattern already used by `safeTransfer` in `DirectDepositV1` to the `approve` call, or use OpenZeppelin's `SafeERC20.safeApprove`. The fix should be applied at line 92:

```solidity
// Before (unsafe):
token.approve(address(endpoint), balance);

// After (safe, consistent with existing safeTransfer pattern):
(bool success, bytes memory data) = address(token).call(
    abi.encodeWithSelector(IIERC20Base.approve.selector, address(endpoint), balance)
);
require(
    success && (data.length == 0 || abi.decode(data, (bool))),
    "Approve failed"
);
```

---

### Proof of Concept

1. Deploy a mock USDT token whose `approve()` always returns `false`.
2. Deploy a `DirectDepositV1` pointing to a mock endpoint that uses `safeTransferFrom`.
3. Transfer 10,000 mock USDT to the DDA.
4. Call `creditDeposit()`.
5. Observe: `approve()` returns `false` (not detected), `depositCollateralWithReferral` reverts due to zero allowance, entire call reverts.
6. Funds remain in the DDA. Call `creditDeposit()` again — same result, indefinitely.
7. Only the owner calling `withdraw()` can recover the tokens.

**Expected trace:**
```
[creditDeposit()]
  ├─ MockUSDT::approve(endpoint, 10000e6) → false  ⚠️ NOT CHECKED
  ├─ endpoint::depositCollateralWithReferral(...)
  │   └─ ERC20Helper::safeTransferFrom(MockUSDT, DDA, endpoint, 10000e6)
  │       └─ MockUSDT::transferFrom → revert: Insufficient allowance
  └─ ← revert  ❌ Funds remain in DDA, user has no recovery path
```

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

**File:** core/contracts/DirectDepositV1.sol (L83-83)
```text
    function creditDeposit() external {
```

**File:** core/contracts/DirectDepositV1.sol (L91-99)
```text
            if (balance != 0) {
                token.approve(address(endpoint), balance);
                endpoint.depositCollateralWithReferral(
                    subaccount,
                    productId,
                    uint128(balance),
                    "-1"
                );
            }
```

**File:** core/contracts/DirectDepositV1.sol (L103-106)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }
```

**File:** core/contracts/libraries/ERC20Helper.sol (L23-42)
```text
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
