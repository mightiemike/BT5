### Title
`IERC4626Base` Is Not ERC4626 Compliant — `wrapVaultAsset` Ignores `deposit()` Return Value, Silently Under-Crediting Vault Shares to User Subaccounts — (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`IERC4626Base` defines only 3 of the ~13 functions required by EIP-4626. The `wrapVaultAsset()` function in `ContractOwner.sol` uses this truncated interface to deposit underlying assets into a vault token and ignores the return value of `deposit()` (the shares minted). Because `wrapVaultAsset()` has no access control, any caller can trigger it. If the vault token has deposit fees or rounding, fewer shares than expected are minted to `directDepositV1`, and the subsequent `creditDeposit()` call credits the subaccount with less collateral than the user's assets were worth — a silent, unrecoverable loss.

---

### Finding Description

`IERC4626Base` is defined as:

```solidity
interface IERC4626Base {
    function asset() external view returns (address);
    function deposit(uint256 assets, address receiver) external returns (uint256);
    function previewDeposit(uint256 assets) external view returns (uint256);
}
``` [1](#0-0) 

The full EIP-4626 standard requires `totalAssets()`, `convertToShares()`, `convertToAssets()`, `maxDeposit()`, `maxMint()`, `maxWithdraw()`, `maxRedeem()`, `previewMint()`, `previewWithdraw()`, `previewRedeem()`, `mint()`, `withdraw()`, `redeem()`, and the `Deposit`/`Withdraw` events — none of which are present.

`wrapVaultAsset()` is the production function that uses this interface:

```solidity
function wrapVaultAsset(bytes32 subaccount, uint32 productId) external {
    ...
    uint256 assetBalance = IERC20Base(assetTokenAddr).balanceOf(directDepositV1);
    if (IERC4626Base(tokenAddr).previewDeposit(assetBalance) != 0) {
        DirectDepositV1(directDepositV1).withdraw(IIERC20Base(assetTokenAddr));
        IERC20Base assetToken = IERC20Base(assetTokenAddr);
        assetToken.approve(tokenAddr, 0);
        assetToken.approve(tokenAddr, assetBalance);
        IERC4626Base(tokenAddr).deposit(assetBalance, directDepositV1);
    }
}
``` [2](#0-1) 

The return value of `deposit()` — the number of shares minted to `directDepositV1` — is discarded. Because `IERC4626Base` is missing `convertToAssets()`, `withdraw()`, and `redeem()`, there is no mechanism within the interface to verify or recover from a shortfall in shares minted.

The function has **no access control modifier** — it is callable by any external address. [3](#0-2) 

After `wrapVaultAsset` runs, `creditDeposit()` in `DirectDepositV1` credits the subaccount based on the vault token balance of `directDepositV1`:

```solidity
uint256 balance = token.balanceOf(address(this));
if (balance != 0) {
    token.approve(address(endpoint), balance);
    endpoint.depositCollateralWithReferral(subaccount, productId, uint128(balance), "-1");
}
``` [4](#0-3) 

If the vault minted fewer shares than `assetBalance` implied (due to deposit fees, rounding, or a max-deposit cap), the subaccount is credited with fewer vault tokens than the user's underlying assets were worth.

---

### Impact Explanation

A user sends underlying asset tokens (e.g., USDC) to their `directDepositV1` address, expecting them to be wrapped into vault shares and credited as collateral. If the vault token registered in the spot engine charges a deposit fee or has rounding that causes `deposit(assetBalance)` to mint fewer shares than `previewDeposit(assetBalance)` predicted, the subaccount receives less collateral than the assets deposited. The difference is permanently locked in the vault with no recovery path through the `IERC4626Base` interface (which lacks `withdraw()` and `redeem()`). The user suffers a direct, unrecoverable loss of collateral value.

---

### Likelihood Explanation

`wrapVaultAsset()` is a public, permissionless function. Any address can call it for any subaccount at any time. The precondition is that a vault token with deposit fees or a non-1:1 deposit ratio is registered as a spot product. EIP-4626 explicitly permits deposit fees and rounding-down of shares, making this a realistic scenario for any fee-bearing vault token listed on the protocol.

---

### Recommendation

1. **Check the return value of `deposit()`**: Compare the shares returned by `deposit()` against `previewDeposit(assetBalance)` and revert if the shortfall exceeds an acceptable tolerance.
2. **Expand `IERC4626Base`** to include at minimum `convertToAssets()`, `withdraw()`, and `redeem()` so the protocol can verify and recover from deposit shortfalls.
3. **Add access control** to `wrapVaultAsset()` (e.g., `onlyOwner` or restrict to the subaccount owner) to prevent griefing via forced deposits at unfavorable vault states.

---

### Proof of Concept

1. A vault token with a 1% deposit fee is registered in the spot engine for `productId = X`.
2. A user sends 1000 USDC to their `directDepositV1` address.
3. An attacker (or anyone) calls `ContractOwner.wrapVaultAsset(subaccount, X)`.
4. `previewDeposit(1000e6)` returns 990 shares (non-zero), so the branch executes.
5. `DirectDepositV1.withdraw(USDC)` transfers 1000 USDC to `ContractOwner`.
6. `ContractOwner` approves the vault for 1000 USDC and calls `deposit(1000e6, directDepositV1)`.
7. The vault charges its 1% fee and mints only 990 shares to `directDepositV1`. The return value `990` is silently discarded.
8. `creditDeposit()` is called; `directDepositV1` holds 990 vault shares, so the subaccount is credited with 990 vault tokens instead of the 1000 USDC equivalent.
9. The user has lost 10 USDC worth of collateral with no recourse through the protocol interface. [5](#0-4) [1](#0-0)

### Citations

**File:** core/contracts/interfaces/IERC4626Base.sol (L4-12)
```text
interface IERC4626Base {
    function asset() external view returns (address);

    function deposit(uint256 assets, address receiver)
        external
        returns (uint256);

    function previewDeposit(uint256 assets) external view returns (uint256);
}
```

**File:** core/contracts/ContractOwner.sol (L510-534)
```text
    function wrapVaultAsset(bytes32 subaccount, uint32 productId) external {
        address payable directDepositV1 = directDepositV1Address[subaccount];
        if (directDepositV1 == address(0)) {
            directDepositV1 = createDirectDepositV1(subaccount);
        }

        address tokenAddr = spotEngine.getToken(productId);
        require(tokenAddr != address(0));

        address assetTokenAddr = IERC4626Base(tokenAddr).asset();
        require(assetTokenAddr != address(0));

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
    }
```

**File:** core/contracts/DirectDepositV1.sol (L90-98)
```text
            uint256 balance = token.balanceOf(address(this));
            if (balance != 0) {
                token.approve(address(endpoint), balance);
                endpoint.depositCollateralWithReferral(
                    subaccount,
                    productId,
                    uint128(balance),
                    "-1"
                );
```
