### Title
Raw `approve()` Call in `DirectDepositV1.creditDeposit()` Breaks Deposit Flow for Non-Standard ERC20 Tokens, Locking User Funds — (File: `core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` uses a direct interface call `token.approve(address(endpoint), balance)` without a low-level safe wrapper. For non-standard ERC20 tokens that do not return a `bool` from `approve()` (e.g., USDT), the ABI decoder reverts, permanently blocking the deposit flow and leaving user tokens stranded in the DDA contract with no self-recovery path.

---

### Finding Description

`DirectDepositV1` is a per-subaccount deposit address contract. Users send tokens to it, and anyone can call `creditDeposit()` to forward those tokens into the Nado protocol via `endpoint.depositCollateralWithReferral()`.

The contract correctly implements a custom `safeTransfer()` helper using a low-level `.call()` that tolerates tokens returning no data: [1](#0-0) 

However, the `creditDeposit()` function does **not** apply the same pattern to `approve()`. It calls `approve()` directly through the `IIERC20Base` interface, which expects a `bool` return value: [2](#0-1) 

The `IIERC20Base` interface declares `approve()` as returning `bool`: [3](#0-2) 

For tokens like USDT that return no data from `approve()`, the Solidity ABI decoder reverts when trying to decode the empty return as `bool`. This causes `creditDeposit()` to revert entirely, blocking the deposit of any token held in the DDA that is non-standard.

The `withdraw()` recovery function is `onlyOwner`, so the user has no self-recovery path: [4](#0-3) 

The same pattern exists in `ContractOwner.wrapVaultAsset()`, which also calls raw `assetToken.approve()` without a safe wrapper and has no access control: [5](#0-4) 

---

### Impact Explanation

A user who sends a non-standard ERC20 token (one whose `approve()` does not return `bool`) to their DirectDepositV1 address will have their tokens permanently stuck. `creditDeposit()` reverts at the `approve()` call, so the tokens cannot be deposited into the protocol. The only recovery path is the owner calling `withdrawFromDirectDepositV1()`: [6](#0-5) 

Until the owner intervenes, the user's funds are locked and cannot be traded or withdrawn. If the owner is unavailable or unresponsive, the funds are permanently inaccessible to the user.

---

### Likelihood Explanation

`creditDeposit()` has no access control and is callable by any address: [7](#0-6) 

The `ContractOwner.creditDepositV1()` function, which is also publicly callable, routes directly to `creditDeposit()`: [8](#0-7) 

If the Nado protocol ever lists a non-standard ERC20 token (USDT is the canonical example), any user who sends it to their DDA and calls `creditDeposit()` will trigger the revert. The likelihood is moderate: it depends on whether a non-standard token is listed, but the protocol's architecture explicitly supports multiple spot tokens via `spotEngine.getProductIds()`, making future listing of such tokens plausible.

---

### Recommendation

Replace the direct `token.approve()` call in `creditDeposit()` with a low-level safe wrapper analogous to the existing `safeTransfer()` helper already present in `DirectDepositV1`:

```solidity
function safeApprove(
    IIERC20Base self,
    address spender,
    uint256 amount
) internal {
    (bool success, bytes memory data) = address(self).call(
        abi.encodeWithSelector(IIERC20Base.approve.selector, spender, amount)
    );
    require(
        success && (data.length == 0 || abi.decode(data, (bool))),
        "Approve failed"
    );
}
```

Then replace `token.approve(address(endpoint), balance)` at line 92 with `safeApprove(token, address(endpoint), balance)`.

Apply the same fix to `ContractOwner.wrapVaultAsset()` lines 530–531, replacing raw `assetToken.approve()` calls with safe wrappers.

---

### Proof of Concept

1. A non-standard ERC20 token (e.g., USDT, which returns no data from `approve()`) is listed as a spot product in the Nado protocol.
2. User sends `N` USDT to their `DirectDepositV1` address.
3. User (or anyone) calls `creditDeposit()` on the DDA.
4. The loop reaches the USDT product. `balance != 0`, so execution proceeds to `token.approve(address(endpoint), balance)` at line 92.
5. USDT's `approve()` executes successfully on-chain but returns no data.
6. Solidity's ABI decoder attempts to decode the empty return as `bool` and reverts.
7. The entire `creditDeposit()` call reverts. The USDT remains in the DDA.
8. The user cannot call `withdraw()` (it is `onlyOwner`).
9. Funds are locked until the owner calls `ContractOwner.withdrawFromDirectDepositV1()`. [9](#0-8)

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

**File:** core/contracts/DirectDepositV1.sol (L103-106)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
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

**File:** core/contracts/ContractOwner.sol (L529-532)
```text
            IERC20Base assetToken = IERC20Base(assetTokenAddr);
            assetToken.approve(tokenAddr, 0);
            assetToken.approve(tokenAddr, assetBalance);
            IERC4626Base(tokenAddr).deposit(assetBalance, directDepositV1);
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
