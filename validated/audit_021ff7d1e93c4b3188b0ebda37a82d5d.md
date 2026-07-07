### Title
Bare `.approve()` Call on Non-Standard ERC20 Tokens Permanently Blocks Deposit Crediting — (`File: core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` calls `token.approve(address(endpoint), balance)` via the `IIERC20Base` interface, which declares `approve` as returning `bool`. Non-standard ERC20 tokens (e.g., mainnet USDT) return no data from `approve`. In Solidity 0.8.x, calling a function through an interface that declares a return type when the callee returns nothing causes an ABI decoding revert. The `ERC20Helper` library provides `safeTransfer` and `safeTransferFrom` using low-level `.call()` with the `data.length == 0 || abi.decode(data, (bool))` guard, but no equivalent `safeApprove` exists. The `approve` call in `creditDeposit()` is unguarded.

---

### Finding Description

`DirectDepositV1.creditDeposit()` is a publicly callable (`external`, no access modifier) function that iterates over all registered spot product tokens and attempts to approve the `endpoint` contract to spend each token balance before calling `depositCollateralWithReferral`. [1](#0-0) 

At line 92, the call is:

```solidity
token.approve(address(endpoint), balance);
``` [2](#0-1) 

`token` is typed as `IIERC20Base`, whose interface declares `approve` as returning `bool`: [3](#0-2) 

When Solidity 0.8.x calls a function through an interface that declares a `bool` return, it expects at least 32 bytes of return data. Tokens like USDT return zero bytes from `approve`. The ABI decoder reverts unconditionally, making `creditDeposit()` permanently non-functional for any spot product whose token is a non-standard ERC20.

The `ERC20Helper` library already implements the correct low-level pattern for `transfer` and `transferFrom`: [4](#0-3) 

But no `safeApprove` counterpart exists in `ERC20Helper`, and `DirectDepositV1` does not import `ERC20Helper` at all.

A secondary instance exists in `ContractOwner.wrapVaultAsset()` (also publicly callable, no access modifier) at lines 530–531: [5](#0-4) 

---

### Impact Explanation

If any non-standard ERC20 token (returning no data from `approve`) is registered as a spot product, every call to `creditDeposit()` reverts. User funds sent to the `DirectDepositV1` contract are permanently locked — the contract holds the token balance but can never approve the endpoint or call `depositCollateralWithReferral`. There is no fallback path. The `withdraw()` function (owner-only) can recover tokens, but the deposit crediting flow is completely broken for affected tokens.

---

### Likelihood Explanation

USDT is one of the most widely used stablecoins and is a common collateral asset in exchange protocols. If Nado lists USDT or any other non-standard ERC20 as a spot product, this bug is triggered on every `creditDeposit()` call. The entry point is publicly callable with no privilege requirement — any user or keeper invoking `creditDeposit()` hits the revert.

---

### Recommendation

Add a `safeApprove` function to `ERC20Helper` using the same low-level `.call()` pattern already used for `safeTransfer`:

```solidity
function safeApprove(
    IERC20Base self,
    address spender,
    uint256 amount
) internal {
    (bool success, bytes memory data) = address(self).call(
        abi.encodeWithSelector(IERC20Base.approve.selector, spender, amount)
    );
    require(
        success && (data.length == 0 || abi.decode(data, (bool))),
        ERR_TRANSFER_FAILED
    );
}
```

Replace the bare `token.approve(...)` calls in `DirectDepositV1.creditDeposit()` and `ContractOwner.wrapVaultAsset()` with `safeApprove`.

---

### Proof of Concept

1. A non-standard ERC20 token (e.g., USDT-like, returning no data from `approve`) is registered as a spot product in `SpotEngine`.
2. A user sends that token to a `DirectDepositV1` contract address.
3. Any caller invokes `DirectDepositV1.creditDeposit()`.
4. The loop reaches the USDT-like token; `token.approve(address(endpoint), balance)` is called via the `IIERC20Base` interface.
5. The token returns zero bytes; Solidity's ABI decoder attempts to decode a `bool` from empty returndata and reverts.
6. The entire `creditDeposit()` transaction reverts. The token balance remains stuck in the `DirectDepositV1` contract with no automated recovery path. [6](#0-5) [7](#0-6)

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

**File:** core/contracts/ContractOwner.sol (L529-532)
```text
            IERC20Base assetToken = IERC20Base(assetTokenAddr);
            assetToken.approve(tokenAddr, 0);
            assetToken.approve(tokenAddr, assetBalance);
            IERC4626Base(tokenAddr).deposit(assetBalance, directDepositV1);
```
