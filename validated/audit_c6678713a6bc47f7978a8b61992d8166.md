### Title
Non-Compliant ERC20 `approve()` in `DirectDepositV1.creditDeposit()` Permanently Locks Tokens — (`File: core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` calls `token.approve(address(endpoint), balance)` directly through the `IIERC20Base` interface, which expects a `bool` return value. Non-compliant ERC20 tokens such as USDT do not return a value on `approve()`, causing the ABI decoder to revert. This permanently blocks the deposit flow for any such token held in the `DirectDepositV1` contract.

---

### Finding Description

`DirectDepositV1.creditDeposit()` iterates over all spot product tokens and, for each token with a non-zero balance, calls `token.approve(address(endpoint), balance)` before depositing:

```solidity
// core/contracts/DirectDepositV1.sol, line 92
token.approve(address(endpoint), balance);
```

The `IIERC20Base` interface declares `approve` as:

```solidity
// core/contracts/DirectDepositV1.sol, line 11
function approve(address spender, uint256 amount) external returns (bool);
```

When this is called against a non-compliant token like USDT (which returns no data), Solidity's ABI decoder expects a 32-byte `bool` return value. Finding none, it reverts. The entire `creditDeposit()` call fails.

Critically, the contract already implements a `safeTransfer` wrapper (lines 69–81) that uses a low-level `call` and tolerates missing return data — but no equivalent `safeApprove` exists. The `ERC20Helper` library (`core/contracts/libraries/ERC20Helper.sol`) similarly provides `safeTransfer` and `safeTransferFrom` but no `safeApprove`.

---

### Impact Explanation

Any USDT (or similarly non-compliant) token that is a registered spot product and is sent to a `DirectDepositV1` address cannot be credited to the subaccount. `creditDeposit()` will revert every time it is called for that token, permanently locking the funds in the `DirectDepositV1` contract with no recovery path for the user (the `withdraw` function is `onlyOwner`).

**Impact: Medium** — funds are locked; no direct theft, but user assets are permanently inaccessible through the normal flow.

---

### Likelihood Explanation

USDT is one of the most widely used stablecoins and a natural candidate for a spot product on a trading protocol. Any user who sends USDT to their `DirectDepositV1` address (e.g., via a UI that directs them to do so) will have their funds stuck. The entry path requires no special privileges.

**Likelihood: Medium** — depends on whether USDT is listed as a spot product, which is a realistic and common configuration.

---

### Recommendation

Replace the direct `approve` call in `creditDeposit()` with a safe low-level call that tolerates missing return data, mirroring the existing `safeTransfer` pattern already present in the same contract:

```diff
// core/contracts/DirectDepositV1.sol
+   function safeApprove(IIERC20Base self, address spender, uint256 amount) internal {
+       (bool success, bytes memory data) = address(self).call(
+           abi.encodeWithSelector(IIERC20Base.approve.selector, spender, amount)
+       );
+       require(
+           success && (data.length == 0 || abi.decode(data, (bool))),
+           "Approve failed"
+       );
+   }

    function creditDeposit() external {
        ...
-           token.approve(address(endpoint), balance);
+           safeApprove(token, address(endpoint), balance);
        ...
    }
```

---

### Proof of Concept

1. USDT is registered as a spot product in `SpotEngine`.
2. A user sends USDT to their `DirectDepositV1` address.
3. Anyone calls `ContractOwner.creditDepositV1(subaccount)` (no access control) or directly calls `DirectDepositV1.creditDeposit()`.
4. Inside `creditDeposit()`, when the loop reaches the USDT product, `token.approve(address(endpoint), balance)` is executed.
5. USDT's `approve` returns no data; Solidity's ABI decoder reverts with a decoding error.
6. The entire transaction reverts. The USDT balance remains in the `DirectDepositV1` contract and cannot be credited to the subaccount.
7. The user's funds are locked; only the owner can call `withdraw`, which is an admin-only recovery path unavailable to the user.

**Relevant lines:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
