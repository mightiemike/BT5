### Title
`DirectDepositV1.creditDeposit()` calls raw `approve()` instead of a safe low-level wrapper, causing permanent revert for non-standard ERC20 tokens — (`File: core/contracts/DirectDepositV1.sol`)

---

### Summary
`DirectDepositV1.creditDeposit()` calls `token.approve(address(endpoint), balance)` through the raw `IIERC20Base` interface, which expects a `bool` return value. Non-standard ERC20 tokens (e.g., USDT) that do not return a boolean from `approve()` will cause the ABI decoder to revert. This permanently blocks collateral crediting for any such token, locking user funds in the `DirectDepositV1` contract.

---

### Finding Description
In `DirectDepositV1.sol`, the `creditDeposit()` function iterates over all spot product tokens and, for each token with a non-zero balance, calls:

```solidity
token.approve(address(endpoint), balance);
endpoint.depositCollateralWithReferral(subaccount, productId, uint128(balance), "-1");
```

The `IIERC20Base` interface declares `approve()` as:

```solidity
function approve(address spender, uint256 amount) external returns (bool);
```

Solidity 0.8.x ABI-decodes the return data strictly: if the token returns zero bytes (as USDT does), the decoder reverts. The `creditDeposit()` function has **no access control** — it is `external` with no modifier — so any caller can trigger it, but it will always revert for non-compliant tokens.

By contrast, the protocol's own `ERC20Helper` library already implements a safe low-level call pattern for `transfer` and `transferFrom` (using `address(self).call(...)` and checking `data.length == 0 || abi.decode(data, (bool))`), but **no equivalent `safeApprove` exists** in `ERC20Helper`. The `DirectDepositV1` contract also implements its own `safeTransfer()` using the same low-level call pattern, yet `approve()` is called directly through the interface. [1](#0-0) [2](#0-1) [3](#0-2) 

---

### Impact Explanation
Any user who sends a non-standard ERC20 token (one whose `approve()` does not return a boolean) to their `DirectDepositV1` address will find that `creditDeposit()` always reverts. The tokens accumulate in the `DirectDepositV1` contract and cannot be credited to the user's subaccount. While the contract owner can recover the raw tokens via `ContractOwner.withdrawFromDirectDepositV1()`, the user's deposit is never credited — their collateral position is never established, and they receive no trading access for those funds. [4](#0-3) [5](#0-4) 

---

### Likelihood Explanation
The `DirectDepositV1` mechanism is designed to accept any spot product token registered in `SpotEngine`. If any such token is a non-standard ERC20 (no boolean return from `approve()`), every call to `creditDeposit()` for that token will revert. The likelihood is medium: it depends on whether a non-standard token is listed as a spot product, but the protocol's multi-chain deployment increases the probability of encountering such tokens. [6](#0-5) 

---

### Recommendation
Replace the direct `token.approve(...)` call with a safe low-level wrapper, consistent with the existing `safeTransfer` pattern already used in `DirectDepositV1` and `ERC20Helper`:

```solidity
// Instead of:
token.approve(address(endpoint), balance);

// Use a safe wrapper:
(bool success, bytes memory data) = address(token).call(
    abi.encodeWithSelector(IIERC20Base.approve.selector, address(endpoint), balance)
);
require(
    success && (data.length == 0 || abi.decode(data, (bool))),
    "Approve failed"
);
```

Alternatively, add a `safeApprove` function to `ERC20Helper` and apply `using ERC20Helper for IERC20Base` in `DirectDepositV1`. [7](#0-6) [8](#0-7) 

---

### Proof of Concept
1. A non-standard ERC20 token (e.g., USDT-style, `approve()` returns nothing) is listed as a spot product in `SpotEngine`.
2. A user sends that token to their `DirectDepositV1` address (obtained via `ContractOwner.createDirectDepositV1(subaccount)`).
3. Anyone calls `DirectDepositV1.creditDeposit()`.
4. The loop reaches the non-standard token: `token.approve(address(endpoint), balance)` is called through `IIERC20Base`, which expects `bool` return data.
5. The token returns 0 bytes; Solidity 0.8.x ABI decoder reverts.
6. The entire `creditDeposit()` call reverts; no collateral is credited.
7. The user's tokens remain locked in `DirectDepositV1` with no path to credit them to their subaccount. [6](#0-5) [2](#0-1)

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
