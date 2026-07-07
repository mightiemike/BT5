### Title
`DirectDepositV1.creditDeposit()` Uses Bare `approve` That Reverts on Non-Standard ERC20s — (`core/contracts/DirectDepositV1.sol`)

### Summary

`DirectDepositV1.creditDeposit()` calls `token.approve(address(endpoint), balance)` through the `IIERC20Base` interface, which declares `approve` as returning `bool`. For tokens like USDT that do not return a value on approval, the Solidity ABI decoder reverts when it tries to decode the missing return data. This permanently blocks the deposit flow for any such token.

### Finding Description

`DirectDepositV1.creditDeposit()` iterates over all spot product tokens and, for each token with a non-zero balance, calls bare `approve` before forwarding the deposit to the endpoint: [1](#0-0) 

The `IIERC20Base` interface declares `approve` as: [2](#0-1) 

When Solidity calls a function declared with a return type, it uses `STATICCALL`/`CALL` and then ABI-decodes the return data. If the token returns no data (as USDT on Ethereum mainnet does), the ABI decoder reverts with an empty-data error. The protocol already recognises this problem for `transfer` and `transferFrom` — `ERC20Helper` implements `safeTransfer` and `safeTransferFrom` using low-level `.call` with `data.length == 0 || abi.decode(data, (bool))` — but no equivalent `safeApprove` exists, and `creditDeposit` does not use the safe pattern: [3](#0-2) 

A second occurrence exists in `ContractOwner.wrapVaultAsset()`, which calls `assetToken.approve(tokenAddr, 0)` and `assetToken.approve(tokenAddr, assetBalance)` through `IERC20Base`, which also declares `approve` as returning `bool`: [4](#0-3) [5](#0-4) 

### Impact Explanation

Any spot product whose underlying token does not return a value on `approve` (e.g., USDT on Ethereum mainnet) will have its `creditDeposit` call revert unconditionally. Because `creditDeposit` is the mechanism by which a `DirectDepositV1` contract forwards received tokens into the protocol, all deposits for that token are permanently bricked — the tokens sit in the `DirectDepositV1` contract and can never be credited to the subaccount. Users lose access to their deposited funds until an admin manually withdraws them via `withdrawFromDirectDepositV1`.

### Likelihood Explanation

`creditDeposit()` has no access modifier and is callable by any address. The protocol explicitly supports USDT as a collateral token. Any user who sends USDT to a `DirectDepositV1` address and then calls `creditDeposit()` (or triggers `ContractOwner.creditDepositV1()`) will hit this revert immediately and reproducibly.

### Recommendation

Add a `safeApprove` helper to `ERC20Helper` using the same low-level `.call` pattern already used for `safeTransfer` and `safeTransferFrom`, and replace the bare `approve` calls in `DirectDepositV1.creditDeposit()` and `ContractOwner.wrapVaultAsset()` with it:

```solidity
function safeApprove(IERC20Base self, address spender, uint256 amount) internal {
    (bool success, bytes memory data) = address(self).call(
        abi.encodeWithSelector(IERC20Base.approve.selector, spender, amount)
    );
    require(
        success && (data.length == 0 || abi.decode(data, (bool))),
        ERR_TRANSFER_FAILED
    );
}
```

### Proof of Concept

1. Deploy a mock USDT token whose `approve` emits no return value (identical to the PoC in the reference report).
2. Register it as a spot product in `SpotEngine`.
3. Send mock USDT to a `DirectDepositV1` address.
4. Call `DirectDepositV1.creditDeposit()` (or `ContractOwner.creditDepositV1(subaccount)`).
5. The transaction reverts at `token.approve(address(endpoint), balance)` because the ABI decoder finds zero return bytes where it expects a `bool`.
6. The USDT balance remains stuck in the `DirectDepositV1` contract and cannot be credited to the subaccount.

### Citations

**File:** core/contracts/DirectDepositV1.sol (L11-11)
```text
    function approve(address spender, uint256 amount) external returns (bool);
```

**File:** core/contracts/DirectDepositV1.sol (L91-98)
```text
            if (balance != 0) {
                token.approve(address(endpoint), balance);
                endpoint.depositCollateralWithReferral(
                    subaccount,
                    productId,
                    uint128(balance),
                    "-1"
                );
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

**File:** core/contracts/ContractOwner.sol (L529-532)
```text
            IERC20Base assetToken = IERC20Base(assetTokenAddr);
            assetToken.approve(tokenAddr, 0);
            assetToken.approve(tokenAddr, assetBalance);
            IERC4626Base(tokenAddr).deposit(assetBalance, directDepositV1);
```

**File:** core/contracts/interfaces/IERC20Base.sol (L41-41)
```text
    function approve(address spender, uint256 value) external returns (bool);
```
