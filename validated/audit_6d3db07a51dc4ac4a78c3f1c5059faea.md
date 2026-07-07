### Title
Direct `approve` Call on Non-Compliant ERC20 in `DirectDepositV1.creditDeposit` Breaks Deposit Flow — (`File: core/contracts/DirectDepositV1.sol`)

### Summary
`DirectDepositV1.creditDeposit()` calls `token.approve(address(endpoint), balance)` directly via the `IIERC20Base` interface, which declares `approve` as returning `bool`. If any registered spot token does not return a boolean from `approve` (non-ERC20-compliant behavior, as seen with USDT on Ethereum mainnet), Solidity's ABI decoder reverts on the empty return data, causing the entire `creditDeposit` call to fail and leaving deposited funds stranded in the DDA contract.

### Finding Description
`DirectDepositV1.creditDeposit()` iterates over all registered spot product IDs and, for each token with a non-zero balance, calls `token.approve(address(endpoint), balance)` directly:

```solidity
// DirectDepositV1.sol line 92
token.approve(address(endpoint), balance);
```

The `IIERC20Base` interface declares `approve` as:

```solidity
function approve(address spender, uint256 amount) external returns (bool);
```

Solidity ^0.8.0 will attempt to ABI-decode the return value. If the token's `approve` implementation does not return a boolean (e.g., USDT on Ethereum mainnet), the decoder reverts with an empty-returndata error. Because the function loops over **all** product IDs, a single non-compliant token with a non-zero balance causes the entire `creditDeposit` call to revert, blocking credit for every token in that DDA.

By contrast, the protocol's own `ERC20Helper` library correctly handles this case using a low-level `.call()` that accepts `data.length == 0` as a success:

```solidity
// ERC20Helper.sol lines 14-20
(bool success, bytes memory data) = address(self).call(
    abi.encodeWithSelector(IERC20Base.transfer.selector, to, amount)
);
require(
    success && (data.length == 0 || abi.decode(data, (bool))),
    ERR_TRANSFER_FAILED
);
```

`creditDeposit` does not use this library; it calls `approve` directly on the raw interface.

The same pattern exists in `ContractOwner.wrapVaultAsset` at lines 530–531, where `assetToken.approve(tokenAddr, 0)` and `assetToken.approve(tokenAddr, assetBalance)` are called directly on `IERC20Base` without going through `ERC20Helper`.

### Impact Explanation
Any DDA holding a non-compliant ERC20 token (one whose `approve` does not return a bool) will have its `creditDeposit` call permanently revert. Funds sent to the DDA are not credited to the subaccount and remain stranded in the DDA contract. Recovery requires the owner to call `ContractOwner.withdrawFromDirectDepositV1`, breaking the intended automated deposit flow. For `wrapVaultAsset`, the vault-wrapping flow for that asset is permanently broken.

### Likelihood Explanation
The Nado protocol is designed to support multiple spot tokens beyond USDC. Any token registered as a spot product whose `approve` does not return a bool triggers this revert. USDT on Ethereum mainnet is the canonical example; bridged USDT variants on OP Stack chains may or may not be compliant depending on the bridge implementation. The protocol's own `ERC20Helper` was written specifically to handle this class of token, confirming the developers are aware of the risk — but `creditDeposit` and `wrapVaultAsset` were not updated to use it.

### Recommendation
Replace the direct `approve` calls with a safe wrapper analogous to `ERC20Helper.safeTransfer`. Add a `safeApprove` function to `ERC20Helper` using the same low-level `.call()` pattern, and use it in:
- `DirectDepositV1.creditDeposit` (line 92)
- `ContractOwner.wrapVaultAsset` (lines 530–531)
- `ContractOwner.depositInsurance` (line 254)

### Proof of Concept

1. A spot token `T` is registered in `SpotEngine` whose `approve` does not return a bool.
2. A user sends `T` to a DDA contract (`DirectDepositV1` instance).
3. Anyone calls `DirectDepositV1.creditDeposit()`.
4. The loop reaches token `T`, calls `token.approve(address(endpoint), balance)`.
5. Solidity attempts to decode the empty return data as `bool` → revert.
6. The entire `creditDeposit` call reverts; no token in the DDA is credited.
7. Funds remain in the DDA; the subaccount receives no credit.

Relevant lines: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** core/contracts/DirectDepositV1.sol (L6-12)
```text
interface IIERC20Base {
    function transfer(address to, uint256 amount) external returns (bool);

    function balanceOf(address account) external view returns (uint256);

    function approve(address spender, uint256 amount) external returns (bool);
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

**File:** core/contracts/ContractOwner.sol (L529-533)
```text
            IERC20Base assetToken = IERC20Base(assetTokenAddr);
            assetToken.approve(tokenAddr, 0);
            assetToken.approve(tokenAddr, assetBalance);
            IERC4626Base(tokenAddr).deposit(assetBalance, directDepositV1);
        }
```
