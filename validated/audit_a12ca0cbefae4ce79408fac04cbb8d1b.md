### Title
Unchecked `bool` Return on High-Level `approve` Call Breaks Collateral Deposit for USDT-Like Tokens — (`File: core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` calls `token.approve(address(endpoint), balance)` as a high-level Solidity call against the `IIERC20Base` interface, which declares `approve` as returning `bool`. For tokens like mainnet USDT that return no data from `approve`, Solidity's ABI decoder reverts when attempting to decode the empty return buffer. This permanently breaks collateral deposit for any USDT-like token routed through the `DirectDepositV1` mechanism.

---

### Finding Description

`IIERC20Base` in `DirectDepositV1.sol` declares `approve` as:

```solidity
function approve(address spender, uint256 amount) external returns (bool);
``` [1](#0-0) 

Inside `creditDeposit()`, the high-level call is made at line 92:

```solidity
token.approve(address(endpoint), balance);
``` [2](#0-1) 

In Solidity `>=0.8.0`, a high-level call to a function declared to return a value causes the compiler to emit ABI-decoding logic on the return buffer. If the callee (e.g., USDT) returns zero bytes, the decoder reverts unconditionally — even though the `approve` itself succeeded on-chain. The return value is also never inspected, so even a `false` return would silently pass if decoding did not revert.

The same pattern appears in `ContractOwner.wrapVaultAsset()` at lines 530–531, where `IERC20Base.approve()` is called directly on the underlying vault asset token:

```solidity
assetToken.approve(tokenAddr, 0);
assetToken.approve(tokenAddr, assetBalance);
``` [3](#0-2) 

The codebase already has a correct safe-transfer pattern in `ERC20Helper` using low-level `.call()` with `data.length == 0 || abi.decode(data, (bool))`: [4](#0-3) 

However, no equivalent `safeApprove` exists, and `creditDeposit()` does not use `ERC20Helper` at all — it uses the raw `IIERC20Base` interface directly.

---

### Impact Explanation

`creditDeposit()` iterates over all registered spot product tokens. If any token in that list is USDT-like (no `bool` return from `approve`), the entire transaction reverts. This means:

- No collateral can be deposited for that subaccount via the `DirectDepositV1` path.
- Funds already sitting in the `DirectDepositV1` contract for that subaccount become permanently stuck — they cannot be credited to the protocol.
- The `wrapVaultAsset()` path is similarly broken for vault assets whose underlying token is USDT-like.

The corrupted state delta is: **token balance held in `DirectDepositV1` is never forwarded to `endpoint.depositCollateralWithReferral`, leaving user collateral inaccessible.**

---

### Likelihood Explanation

`creditDepositV1(bytes32 subaccount)` in `ContractOwner` has **no access control** — any unprivileged caller can invoke it: [5](#0-4) 

Likewise `wrapVaultAsset` has no access control: [6](#0-5) 

USDT is one of the highest-volume collateral tokens on any EVM-compatible chain. If the protocol lists USDT as a supported spot product (which is the expected production configuration), this bug triggers deterministically on every `creditDeposit()` call for any subaccount holding USDT.

---

### Recommendation

Replace the bare high-level `approve` call in `creditDeposit()` with a low-level safe-approve pattern analogous to `ERC20Helper.safeTransfer`:

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

Apply the same fix to `ContractOwner.wrapVaultAsset()` lines 530–531 by adding a `safeApprove` to `ERC20Helper` and using it via the `using ERC20Helper for IERC20Base` directive already present in `ContractOwner`. [7](#0-6) 

---

### Proof of Concept

1. USDT is listed as a supported spot product in `SpotEngine`.
2. A user sends USDT to their `DirectDepositV1` contract address.
3. Any caller invokes `ContractOwner.creditDepositV1(subaccount)`.
4. `DirectDepositV1.creditDeposit()` reaches the USDT product iteration.
5. `token.approve(address(endpoint), balance)` is executed as a high-level call.
6. USDT's `approve` returns no data; Solidity's ABI decoder reverts.
7. The entire `creditDeposit()` call reverts; USDT remains stuck in the `DirectDepositV1` contract and is never credited to the subaccount. [8](#0-7)

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

**File:** core/contracts/ContractOwner.sol (L24-24)
```text
    using ERC20Helper for IERC20Base;
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

**File:** core/contracts/ContractOwner.sol (L510-510)
```text
    function wrapVaultAsset(bytes32 subaccount, uint32 productId) external {
```

**File:** core/contracts/ContractOwner.sol (L529-531)
```text
            IERC20Base assetToken = IERC20Base(assetTokenAddr);
            assetToken.approve(tokenAddr, 0);
            assetToken.approve(tokenAddr, assetBalance);
```

**File:** core/contracts/libraries/ERC20Helper.sol (L14-20)
```text
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(IERC20Base.transfer.selector, to, amount)
        );
        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
```
