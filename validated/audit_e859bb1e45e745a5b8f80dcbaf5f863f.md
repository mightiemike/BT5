### Title
Raw `IIERC20Base.approve()` in `DirectDepositV1.creditDeposit()` Permanently Bricks Deposit Crediting for Non-Bool-Returning Tokens — (File: `core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` calls `token.approve(address(endpoint), balance)` via the raw `IIERC20Base` interface, which Solidity's ABI decoder expects to return a `bool`. If any supported spot-product token does not return a `bool` from `approve()` (e.g., USDT and its derivatives), the decoder reverts. The same contract already implements a `safeTransfer()` low-level wrapper that tolerates non-bool-returning tokens for `transfer()`, but no equivalent protection is applied to `approve()`. Because `creditDeposit()` carries no access control, any caller can trigger the revert path, and the deposit-credit mechanism for the affected subaccount is permanently bricked.

---

### Finding Description

`DirectDepositV1` defines a local `safeTransfer()` helper that uses a low-level `.call()` and explicitly handles the `data.length == 0` case for non-bool-returning tokens: [1](#0-0) 

However, `creditDeposit()` — the function that actually credits collateral to the protocol — calls `approve()` directly through the typed `IIERC20Base` interface: [2](#0-1) 

The `IIERC20Base` interface declares `approve` as returning `bool`: [3](#0-2) 

When the underlying token returns no data (empty return), Solidity's generated ABI decoder attempts to decode a `bool` from zero bytes and reverts. The `safeTransfer` pattern present in the same file is the correct fix but was never applied to `approve`.

The broader `ERC20Helper` library used by the rest of the protocol also provides `safeTransfer` and `safeTransferFrom` via low-level calls, but contains no `safeApprove`: [4](#0-3) 

---

### Impact Explanation

If any spot-product token registered in `SpotEngine` does not return a `bool` from `approve()`, every call to `creditDeposit()` on any `DirectDepositV1` instance will revert unconditionally at line 92. Tokens already sitting in the `DirectDepositV1` contract cannot be credited to the subaccount via the normal path. The only recovery is the owner-gated `withdraw()` function, which pulls tokens back out but does not credit them to the protocol subaccount — meaning the deposit flow is permanently broken for that token/subaccount pair.

---

### Likelihood Explanation

USDT and USDT-derivative tokens (including USDT0, the bridged OFT variant) are among the most commonly listed collateral assets on perpetuals exchanges. The protocol already handles USDC-E → USDC migration on chain 57073 (Ink), indicating active multi-stablecoin support. If USDT is ever listed as a spot product, every `DirectDepositV1` instance for every subaccount that receives USDT will be permanently unable to credit deposits. The entry path requires no privilege: `creditDeposit()` is `external` with no `onlyOwner` or similar guard. [5](#0-4) 

---

### Recommendation

Replace the raw `token.approve(...)` call in `creditDeposit()` with a low-level call that tolerates empty return data, mirroring the existing `safeTransfer()` pattern in the same file:

```diff
-token.approve(address(endpoint), balance);
+safeApprove(token, address(endpoint), balance);
```

Add a `safeApprove` helper alongside `safeTransfer`:

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

Alternatively, extend `ERC20Helper` with a `safeApprove` and use it consistently across the codebase, including the analogous raw `approve` calls in `ContractOwner.wrapVaultAsset()` at lines 530–531. [6](#0-5) 

---

### Proof of Concept

1. A non-bool-returning token (e.g., USDT) is listed as a supported spot product in `SpotEngine`.
2. A user sends USDT to their `DirectDepositV1` address.
3. Any caller invokes `creditDeposit()` on that `DirectDepositV1` instance.
4. The loop reaches the USDT product; `balance != 0` is true.
5. `token.approve(address(endpoint), balance)` is called; USDT's `approve()` executes but returns no data.
6. Solidity's ABI decoder attempts to decode a `bool` from empty return data → reverts.
7. The entire `creditDeposit()` call reverts; USDT remains stuck in the `DirectDepositV1` contract and cannot be credited to the subaccount through the normal deposit flow. [7](#0-6)

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

**File:** core/contracts/ContractOwner.sol (L529-531)
```text
            IERC20Base assetToken = IERC20Base(assetTokenAddr);
            assetToken.approve(tokenAddr, 0);
            assetToken.approve(tokenAddr, assetBalance);
```
