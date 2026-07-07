### Title
`DirectDepositV1#creditDeposit()` Reverts for Non-Standard ERC20 Tokens Due to High-Level `approve()` Call — (`core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` calls `token.approve()` as a high-level Solidity call against the `IIERC20Base` interface, which declares `approve` as returning `bool`. For non-standard ERC20 tokens (e.g., USDT) that return no data from `approve`, the ABI decoder reverts with `"function returned an unexpected amount of data"`. This permanently blocks all deposits through `DirectDepositV1` whenever such a token has a non-zero balance in the contract.

---

### Finding Description

`DirectDepositV1` defines its local token interface as:

```solidity
function approve(address spender, uint256 amount) external returns (bool);
``` [1](#0-0) 

Inside `creditDeposit()`, the approval is issued as a direct high-level call:

```solidity
token.approve(address(endpoint), balance);
``` [2](#0-1) 

Because this is a typed high-level call (not a low-level `.call()`), the Solidity ABI decoder unconditionally expects a 32-byte `bool` return value. If the token returns zero bytes — as USDT and several other widely-deployed ERC20s do — the EVM reverts with `"function returned an unexpected amount of data"`.

The contract already recognises this class of problem for `transfer`: the `safeTransfer` helper in the same file uses a low-level `.call()` and explicitly tolerates an empty return:

```solidity
(bool success, bytes memory data) = address(self).call(
    abi.encodeWithSelector(IIERC20Base.transfer.selector, to, amount)
);
require(
    success && (data.length == 0 || abi.decode(data, (bool))),
    "Transfer failed"
);
``` [3](#0-2) 

The same defensive pattern was applied to `transfer` but was omitted for `approve` on line 92, creating an inconsistency that is the root cause.

The loop in `creditDeposit()` has no per-token error isolation:

```solidity
for (uint256 i = 0; i < productIds.length; i++) {
    ...
    if (balance != 0) {
        token.approve(address(endpoint), balance);   // reverts here
        endpoint.depositCollateralWithReferral(...);
    }
}
``` [4](#0-3) 

A single non-standard token with a non-zero balance causes the entire transaction to revert, blocking deposits for every other token in the same call.

---

### Impact Explanation

Any user can call `creditDeposit()` (it is `external` with no access control). When a non-standard ERC20 registered as a SpotEngine product accumulates a balance in `DirectDepositV1` — either through a direct transfer or through the normal deposit flow — every subsequent `creditDeposit()` call reverts. Funds sent to the contract (including native ETH that was already wrapped to WETH by `receive()`) accumulate without being credited to the target subaccount. The owner can recover tokens via `withdraw()`, but the core deposit functionality of the contract is rendered permanently non-operational for the affected product set. This constitutes a concrete asset-routing failure: deposited value is not credited to the intended protocol subaccount.

---

### Likelihood Explanation

USDT is one of the most widely integrated ERC20 tokens and is a canonical example of a token that returns no data from `approve`. If USDT or any similarly non-compliant token is listed as a SpotEngine product (a routine governance action), the condition is immediately and deterministically triggered by any user who calls `creditDeposit()` after that token accrues a balance. No privileged access, no special timing, and no attacker-controlled state beyond sending tokens to the contract is required.

---

### Recommendation

Replace the direct high-level `approve` call with a low-level safe-approve pattern consistent with the existing `safeTransfer` helper in the same file:

```solidity
(bool success, bytes memory data) = address(token).call(
    abi.encodeWithSelector(IIERC20Base.approve.selector, address(endpoint), balance)
);
require(
    success && (data.length == 0 || abi.decode(data, (bool))),
    "Approve failed"
);
```

Alternatively, use OpenZeppelin's `SafeERC20.safeApprove` / `forceApprove`.

---

### Proof of Concept

1. A non-standard ERC20 (e.g., USDT — `approve` returns no data) is registered as a product in `SpotEngine`.
2. A user sends that token directly to `DirectDepositV1`, or it accumulates there through normal operation.
3. Any caller invokes `creditDeposit()`.
4. The loop reaches the non-standard token, executes `token.approve(address(endpoint), balance)` as a high-level typed call.
5. The token's `approve` returns zero bytes; the Solidity ABI decoder expects 32 bytes (`bool`).
6. The EVM reverts: `"function returned an unexpected amount of data"`.
7. The entire `creditDeposit()` call reverts — no token for any product is deposited, and all accumulated balances remain stranded in `DirectDepositV1`.

### Citations

**File:** core/contracts/DirectDepositV1.sol (L11-11)
```text
    function approve(address spender, uint256 amount) external returns (bool);
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
