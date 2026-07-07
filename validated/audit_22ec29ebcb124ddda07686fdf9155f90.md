### Title
`DirectDepositV1#creditDeposit()` Direct `approve()` Call on Non-Returning ERC20 Tokens Always Reverts, Permanently Locking Deposited Funds — (`File: core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` calls `token.approve()` via a direct Solidity interface call that expects a `bool` return value. For ERC20 tokens that do not return data from `approve()` (e.g., USDT-style tokens), the Solidity ABI decoder reverts on empty return data. The rest of the codebase uses `ERC20Helper` with low-level calls to handle this safely for `transfer` and `transferFrom`, but `approve` in `DirectDepositV1` bypasses this pattern entirely.

---

### Finding Description

`DirectDepositV1` defines a local interface `IIERC20Base` with `approve` declared as `returns (bool)`: [1](#0-0) 

Inside `creditDeposit()`, the approval is issued as a direct Solidity call: [2](#0-1) 

In Solidity ≥0.8.0, a direct call to an interface function declared as `returns (bool)` causes the ABI decoder to attempt to decode a `bool` from the return buffer. If the token returns zero bytes (as USDT and similar tokens do), the decoder reverts unconditionally.

By contrast, `transfer` in the same contract is handled safely via a low-level call: [3](#0-2) 

The protocol-wide `ERC20Helper` library applies the same safe pattern for `transfer` and `transferFrom`: [4](#0-3) 

No `safeApprove` equivalent exists in `ERC20Helper`, and `DirectDepositV1` does not use `ERC20Helper` for its `approve` call.

---

### Impact Explanation

For any product registered in `SpotEngine` whose underlying token does not return a value from `approve()`:

1. Tokens sent to the DDA contract accumulate in its balance.
2. Any caller of `creditDeposit()` triggers the revert at the `approve` line.
3. The deposit is never forwarded to the `Endpoint`.
4. Funds are permanently locked in the DDA contract with no recovery path (the `withdraw()` owner function only calls `safeTransfer`, not `approve`, so it is unaffected — but the deposit credit path is permanently broken for that token).

**Corrupted state delta:** `token.balanceOf(DDA) > 0` indefinitely; `Endpoint` never receives the deposit; the subaccount balance is never credited.

---

### Likelihood Explanation

`creditDeposit()` has no access control — any address can call it. The trigger requires only that a non-returning ERC20 token (USDT-style) is registered as a product in `SpotEngine`. On EVM-compatible chains, such tokens are common. The `SpotEngine.addOrUpdateProduct()` function accepts any token address set by the owner, and the protocol's design explicitly supports multiple collateral types. Once such a token is registered and funds arrive at the DDA, every `creditDeposit()` call reverts deterministically.

---

### Recommendation

Replace the direct `approve` call in `creditDeposit()` with a low-level safe call pattern consistent with `ERC20Helper.safeTransfer`:

```solidity
// Instead of:
token.approve(address(endpoint), balance);

// Use a safe low-level call:
(bool success, bytes memory data) = address(token).call(
    abi.encodeWithSelector(IIERC20Base.approve.selector, address(endpoint), balance)
);
require(
    success && (data.length == 0 || abi.decode(data, (bool))),
    "Approve failed"
);
```

Alternatively, add a `safeApprove` function to `ERC20Helper` and use it here.

---

### Proof of Concept

1. Owner registers a USDT-style token (no return value from `approve`) as a product in `SpotEngine`.
2. A user sends tokens directly to the DDA contract address.
3. Anyone calls `DirectDepositV1.creditDeposit()`.
4. Execution reaches `token.approve(address(endpoint), balance)` at line 92.
5. The token's `approve()` executes successfully on-chain but returns zero bytes.
6. Solidity's ABI decoder attempts to decode a `bool` from empty return data → reverts.
7. The deposit is never credited; funds remain locked in the DDA forever. [5](#0-4)

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
