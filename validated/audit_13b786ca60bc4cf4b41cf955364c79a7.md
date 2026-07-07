### Title
Missing Slippage Control on ERC4626 `deposit` Call in `wrapVaultAsset` — (`core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.wrapVaultAsset` converts underlying ERC4626 asset tokens held in a user's `DirectDepositV1` contract into vault shares by calling `IERC4626Base(tokenAddr).deposit(assetBalance, directDepositV1)`. The return value (shares minted) is silently discarded and no minimum-shares guard exists. The function carries no access-control modifier, so any external address can trigger it at an arbitrarily unfavorable exchange rate. The resulting vault shares are subsequently credited to the subaccount as collateral via `creditDepositV1`, meaning the subaccount permanently receives less collateral than the underlying assets it provided.

---

### Finding Description

`ContractOwner.wrapVaultAsset` is an `external` function with no `onlyOwner` or similar modifier. [1](#0-0) 

Its execution path is:

1. Reads `assetBalance` — the full underlying-token balance held by the subaccount's `DirectDepositV1` contract.
2. Calls `previewDeposit(assetBalance) != 0` as the only guard — this only rejects a zero-share result, not a below-minimum one.
3. Pulls all underlying tokens out of `DirectDepositV1` via `withdraw`.
4. Calls `IERC4626Base(tokenAddr).deposit(assetBalance, directDepositV1)` and **discards the returned `shares` value entirely**. [2](#0-1) 

The `IERC4626Base` interface confirms `deposit` returns a `uint256` shares value that is never captured here. [3](#0-2) 

After `wrapVaultAsset` completes, the vault shares sit in `DirectDepositV1`. When `creditDepositV1` is called next, `DirectDepositV1.creditDeposit` reads the vault-token balance and calls `endpoint.depositCollateralWithReferral` with exactly that balance. [4](#0-3) 

Because the shares amount is whatever the vault returned — with no floor check — the subaccount is credited with however many shares the vault chose to mint, which can be materially less than the user expected.

---

### Impact Explanation

The vault shares minted by `deposit` become the collateral balance credited to the subaccount inside the Nado spot engine. If the vault's exchange rate has moved adversely (e.g., due to a donation attack inflating `totalAssets`, fee accrual, or any other mechanism that increases the assets-per-share ratio), the subaccount receives fewer shares — and therefore less collateral — than the underlying assets it provided warrant. This is a direct, permanent asset loss: the underlying tokens are consumed by the vault, but the subaccount's on-chain collateral balance is understated relative to what was deposited. [5](#0-4) 

---

### Likelihood Explanation

`wrapVaultAsset` is callable by **any** external address with no restriction. [1](#0-0) 

This creates two realistic trigger paths:

1. **Self-inflicted**: The subaccount owner calls `wrapVaultAsset` themselves. Between their off-chain `previewDeposit` check and on-chain execution, the vault rate shifts (front-run or organic movement), and they receive fewer shares than expected with no ability to revert.
2. **Third-party trigger**: Any external actor can call `wrapVaultAsset` for any subaccount at any time. An adversary can wait for or manufacture an unfavorable vault rate and then call the function, forcing the conversion at a bad rate before the subaccount owner can act.

Both paths require no privileged access.

---

### Recommendation

Capture the return value of `deposit` and compare it against a caller-supplied `minShares` parameter. Revert if the actual shares minted fall below the minimum:

```solidity
function wrapVaultAsset(
    bytes32 subaccount,
    uint32 productId,
    uint256 minShares   // <-- add slippage parameter
) external {
    ...
    uint256 sharesReceived = IERC4626Base(tokenAddr).deposit(
        assetBalance,
        directDepositV1
    );
    require(sharesReceived >= minShares, "INSUFFICIENT_SHARES");
}
```

Additionally, consider adding an `onlyOwner` or subaccount-owner access control to prevent third parties from triggering the conversion at an unfavorable time.

---

### Proof of Concept

1. Alice deposits 1 000 USDC into her `DirectDepositV1` contract, intending to wrap it into a yield-bearing vault token (e.g., `aUSDC`) and deposit it as collateral.
2. The vault currently has a 1:1 rate (`previewDeposit(1000e6) == 1000e18` shares).
3. An attacker donates a large amount of USDC directly to the vault contract, inflating `totalAssets` and raising the assets-per-share price to 2:1.
4. The attacker (or Alice herself, after the rate shift) calls `ContractOwner.wrapVaultAsset(aliceSubaccount, productId)`.
5. `IERC4626Base(tokenAddr).deposit(1000e6, directDepositV1)` now mints only `500e18` shares instead of `1000e18`. The return value is discarded.
6. `creditDepositV1` is called; Alice's subaccount is credited with `500e18` vault shares worth of collateral instead of `1000e18`.
7. Alice has permanently lost half her expected collateral credit with no recourse. [6](#0-5)

### Citations

**File:** core/contracts/ContractOwner.sol (L510-510)
```text
    function wrapVaultAsset(bytes32 subaccount, uint32 productId) external {
```

**File:** core/contracts/ContractOwner.sol (L522-533)
```text
        uint256 assetBalance = IERC20Base(assetTokenAddr).balanceOf(
            directDepositV1
        );
        if (IERC4626Base(tokenAddr).previewDeposit(assetBalance) != 0) {
            DirectDepositV1(directDepositV1).withdraw(
                IIERC20Base(assetTokenAddr)
            );
            IERC20Base assetToken = IERC20Base(assetTokenAddr);
            assetToken.approve(tokenAddr, 0);
            assetToken.approve(tokenAddr, assetBalance);
            IERC4626Base(tokenAddr).deposit(assetBalance, directDepositV1);
        }
```

**File:** core/contracts/interfaces/IERC4626Base.sol (L7-9)
```text
    function deposit(uint256 assets, address receiver)
        external
        returns (uint256);
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
