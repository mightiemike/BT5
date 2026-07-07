### Title
ERC4626 Share Inflation via Unvalidated `deposit` Return in `wrapVaultAsset` Enables Asset Loss for Depositors — (File: `core/contracts/ContractOwner.sol`)

---

### Summary

The permissionless `wrapVaultAsset` function in `ContractOwner.sol` wraps underlying ERC4626 vault assets held in a user's `DirectDepositV1` deposit address into vault shares. The function uses `previewDeposit(assetBalance) != 0` as its only guard before calling `deposit`, but does not validate the actual shares received from `deposit` and does not enforce a minimum deposit amount. An attacker who executes the classic ERC4626 first-depositor inflation attack against an integrated vault can cause a victim's deposited assets to be exchanged for a disproportionately small number of vault shares, resulting in direct asset loss.

---

### Finding Description

`wrapVaultAsset` in `ContractOwner.sol` (lines 510–534) is callable by any unprivileged address with no access control:

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
```

The function:
1. Reads `assetBalance` from `directDepositV1`
2. Checks only `previewDeposit(assetBalance) != 0` — this returns non-zero even when only 1 share would be minted for an arbitrarily large deposit
3. Transfers all assets from `directDepositV1` to `ContractOwner`, then deposits them into the ERC4626 vault, sending shares back to `directDepositV1`
4. **Does not validate the return value of `deposit`** (actual shares minted)
5. **Does not enforce a minimum deposit amount**, unlike `depositCollateral` which calls `isValidDepositAmount` → `checkMinDeposit`

The companion function `creditDepositV1` is also permissionless:

```solidity
function creditDepositV1(bytes32 subaccount) external {
    ...
    DirectDepositV1(directDepositV1).creditDeposit();
}
```

This means an attacker controls the full execution path: inflate the vault, call `wrapVaultAsset`, call `creditDepositV1`.

The classic ERC4626 inflation attack applies directly:

- Attacker is the first depositor in the integrated ERC4626 vault, depositing 1 wei and receiving 1 share.
- Attacker donates `X` underlying tokens directly to the vault (no shares minted), inflating the share price to `(1 + X)` tokens per share.
-