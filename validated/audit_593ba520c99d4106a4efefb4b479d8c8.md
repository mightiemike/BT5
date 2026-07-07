### Title
Unsafe `approve` Call in `DirectDepositV1.creditDeposit()` Can Permanently Lock Token Balances in the DDA - (File: `core/contracts/DirectDepositV1.sol`)

### Summary

`DirectDepositV1.creditDeposit()` uses a direct high-level `approve` call that expects a `bool` return value. For any registered product token whose `approve` does not return a `bool` (e.g., USDT-style non-standard tokens), the ABI decoder reverts, making `creditDeposit()` permanently non-functional for that token and locking any balance sent to the DDA until privileged admin rescue.

### Finding Description

`DirectDepositV1` defines its own custom interface `IIERC20Base` with `approve` declared as returning `bool`: [1](#0-0) 

Inside `creditDeposit()`, the contract calls `token.approve(address(endpoint), balance)` as a high-level Solidity call: [2](#0-1) 

When Solidity makes a high-level call to a function declared as `returns (bool)`, it ABI-decodes the return data. If the token's `approve` returns no data (empty returndata), the ABI decoder reverts with a decoding error. This is the same class of bug as H01 — using a concrete interface expectation that not all ERC20 tokens satisfy.

By contrast, the `safeTransfer` helper in the same contract correctly handles non-returning tokens via a low-level `.call` with explicit `data.length == 0` handling: [3](#0-2) 

No equivalent `safeApprove` wrapper exists. The `approve` at line 92 is unguarded.

The main protocol's `ERC20Helper` library also uses the safe low-level pattern for `transfer` and `transferFrom`: [4](#0-3) 

But `approve` is never wrapped safely anywhere in the codebase.

### Impact Explanation

Any registered spot product token whose `approve` does not return a `bool` (e.g., USDT on many chains, or any custom vault token) will cause `creditDeposit()` to revert unconditionally. Token balances sent to the DDA for that product are locked inside `DirectDepositV1` and cannot be forwarded to the endpoint. Recovery requires the multisig owner to call `ContractOwner.withdrawFromDirectDepositV1()`: [5](#0-4) 

Until that admin action occurs, user funds are inaccessible. If the multisig is slow or unavailable, the lockup is indefinite. Additionally, because `creditDeposit()` iterates all product IDs in a single loop, a single non-standard token causes the entire loop to revert, blocking deposits for all tokens in that DDA simultaneously.

### Likelihood Explanation

`creditDeposit()` is permissionless — any caller can trigger it: [6](#0-5) 

`ContractOwner.creditDepositV1()` also calls it without access control: [7](#0-6) 

The trigger requires a registered product token with non-standard `approve`. USDT (no return value from `approve`) is a widely used collateral token on many EVM chains. If Nado ever lists such a token as a spot product, any user who sends it to their DDA will have their funds locked. Likelihood is **medium** given the protocol's multi-chain deployment trajectory.

### Recommendation

Replace the direct `token.approve(...)` call in `creditDeposit()` with a safe low-level approve wrapper analogous to the existing `safeTransfer`:

```solidity
function safeApprove(IIERC20Base self, address spender, uint256 amount) internal {
    (bool success, bytes memory data) = address(self).call(
        abi.encodeWithSelector(IIERC20Base.approve.selector, spender, amount)
    );
    require(
        success && (data.length == 0 || abi.decode(data, (bool))),
        "Approve failed"
    );
}
```

Then replace line 92 with `safeApprove(token, address(endpoint), balance)`.

### Proof of Concept

1. A spot product is registered whose underlying token's `approve(address,uint256)` returns no data (e.g., USDT-style).
2. A user sends that token directly to their DDA address.
3. Anyone calls `ContractOwner.creditDepositV1(subaccount)` or `DirectDepositV1.creditDeposit()` directly.
4. The loop reaches the non-standard token, calls `token.approve(address(endpoint), balance)`, and the ABI decoder reverts on empty returndata.
5. The entire transaction reverts; the token balance remains in the DDA.
6. All subsequent calls to `creditDeposit()` for this DDA also revert (same loop, same token).
7. User funds are locked until the multisig calls `withdrawFromDirectDepositV1`.

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

**File:** core/contracts/DirectDepositV1.sol (L83-100)
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
