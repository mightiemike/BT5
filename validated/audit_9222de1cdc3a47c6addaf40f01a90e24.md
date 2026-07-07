### Title
Silent Success of Low-Level ERC20 Calls to Codeless Token Addresses Enables Phantom Deposits — (`File: core/contracts/libraries/ERC20Helper.sol`)

---

### Summary

`ERC20Helper.safeTransfer` and `ERC20Helper.safeTransferFrom` use a low-level `.call()` pattern with a return-data check of `success && (data.length == 0 || abi.decode(data, (bool)))`. At the EVM level, a call to an address with no deployed code returns `success = true` and empty return data (`data.length == 0`). The check therefore passes silently, making the library believe a token transfer succeeded when no tokens moved at all.

---

### Finding Description

Both functions in `ERC20Helper.sol` share the same structure:

```solidity
(bool success, bytes memory data) = address(self).call(
    abi.encodeWithSelector(IERC20Base.transfer.selector, to, amount)
);
require(
    success && (data.length == 0 || abi.decode(data, (bool))),
    ERR_TRANSFER_FAILED
);
``` [1](#0-0) [2](#0-1) 

When `address(self)` has no deployed bytecode:
- The EVM returns `success = true` (standard EVM behavior for calls to EOAs/empty accounts).
- `data` is empty, so `data.length == 0` is `true`.
- The `require` evaluates to `require(true && true)` and passes.

No actual token transfer occurs, but the caller receives no revert signal.

The identical pattern is also present in `DirectDepositV1.sol`'s internal `safeTransfer`: [3](#0-2) 

---

### Impact Explanation

`ERC20Helper.safeTransferFrom` is the library used by the core protocol (Clearinghouse) to pull collateral from users during deposit. If a registered token's contract address has no code at call time (e.g., the token was self-destructed, or a product was misconfigured with an address that has no code), `safeTransferFrom` silently succeeds. The protocol's internal accounting credits the depositor's subaccount with the full collateral amount, but no tokens are actually transferred into the protocol. The attacker then holds a fully credited subaccount balance backed by zero real collateral, which can be used to withdraw real assets from other products, directly draining protocol funds.

The same logic applies to `safeTransfer` during withdrawals: a withdrawal to a codeless token address would silently succeed, decrementing the user's on-chain balance without delivering tokens, causing a loss to the withdrawing user.

---

### Likelihood Explanation

The trigger requires a token address registered in the protocol to have no deployed code. This can occur via:
1. **ERC-20 token self-destruct** — rare but possible on chains that still support `SELFDESTRUCT`.
2. **Misconfigured product registration** — an admin or deployer registers a product with a token address that has no code (e.g., a typo or a pre-deployment address).

The second scenario is realistic during protocol upgrades or new product onboarding. An unprivileged user who monitors the mempool or product registry can immediately exploit the window between product registration and token deployment. No privileged access is required to trigger the deposit path.

---

### Recommendation

Add an explicit code-existence check before executing the low-level call in both `safeTransfer` and `safeTransferFrom` in `ERC20Helper.sol`, and in the internal `safeTransfer` in `DirectDepositV1.sol`:

```solidity
require(address(self).code.length > 0, "ERC20Helper: call to non-contract");
```

Alternatively, replace the low-level call pattern with OpenZeppelin's `SafeERC20` library (`safeTransfer` / `safeTransferFrom`), which performs this check internally via `Address.functionCall`. [4](#0-3) [5](#0-4) 

---

### Proof of Concept

1. Deploy the Nado protocol. Register a new spot product whose token address is a known EOA or a contract address that has not yet been deployed (e.g., a CREATE2 address before deployment).
2. Call `Endpoint.depositCollateral` (or the equivalent entry point) for that product with `amount = 1000e6`.
3. Internally, `Clearinghouse` calls `ERC20Helper.safeTransferFrom(token, msg.sender, clearinghouse, 1000e6)`.
4. Because `token` has no code, the low-level call returns `success=true`, `data=[]`. The `require` passes.
5. The subaccount is credited with `1000e6` units of collateral.
6. The attacker calls `Endpoint.withdrawCollateral` for a different, real product (e.g., USDC), draining real tokens from the protocol.

No tokens were ever deposited; the protocol's insurance fund or other depositors bear the loss. [1](#0-0) [3](#0-2)

### Citations

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
