### Title
ERC4626 Share Price Inflation Silently Blocks `wrapVaultAsset` Collateral Crediting — (`File: core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.wrapVaultAsset` guards the entire ERC4626 deposit path behind a single `previewDeposit(assetBalance) != 0` check. An attacker can donate underlying tokens directly to the ERC4626 vault to inflate its share price, causing `previewDeposit` to round down to zero for any realistic user deposit amount. When this happens the function silently exits without wrapping or crediting the user's assets, leaving them permanently stranded in the `DirectDepositV1` escrow contract.

---

### Finding Description

`ContractOwner.wrapVaultAsset` is the on-chain step that converts a user's raw underlying tokens (sitting in their `DirectDepositV1` address) into ERC4626 vault shares so they can be credited as collateral on the Nado subaccount.

The critical guard is:

```solidity
// ContractOwner.sol line 525
if (IERC4626Base(tokenAddr).previewDeposit(assetBalance) != 0) {
    DirectDepositV1(directDepositV1).withdraw(IIERC20Base(assetTokenAddr));
    ...
    IERC4626Base(tokenAddr).deposit(assetBalance, directDepositV1);
}
```

For a standard ERC4626 vault, `previewDeposit(assets)` computes:

```
shares = assets * totalSupply / totalAssets
```

An attacker who transfers underlying tokens **directly** to the vault contract (not through `deposit`) increases `totalAssets` without minting any new shares. If the attacker inflates `totalAssets` such that:

```
assetBalance * totalSupply < totalAssets
```

then integer division truncates the result to `0`, the `if` branch is never entered, and the function returns silently with no error, no event, and no deposit.

The companion readiness check `isWrapVaultAssetReady` (lines 548–551) performs the identical `previewDeposit` call and returns `false` when the result is zero, so the keeper bot that drives the deposit pipeline also stops processing the user's deposit:

```solidity
// ContractOwner.sol lines 548-561
uint256 wrappedBalance = IERC4626Base(tokenAddr).previewDeposit(assetBalance);
if (wrappedBalance != 0) {
    ...
    return _isDepositAmountReady(...);
}
return false;
```

The result is a two-layer blockade: the keeper never calls `wrapVaultAsset`, and even if it did, the function would silently no-op.

---

### Impact Explanation

A user who sends underlying tokens to their `DirectDepositV1` address expecting them to be wrapped and credited as collateral receives **zero shares and zero collateral credit**. Their tokens remain locked in the `DirectDepositV1` contract. The only recovery path is the owner-gated `withdrawFromDirectDepositV1`, which requires multisig intervention. Until then the user has lost effective use of their capital: they cannot trade, post margin, or open positions with those funds. If the attacker sustains the inflation (e.g. via a flash loan rolled over each block, or a one-time donation large enough to persist), the denial is indefinite.

---

### Likelihood Explanation

`wrapVaultAsset` carries no access control modifier — any externally owned account can call it. The attacker does not need to front-run a specific transaction; a single direct token transfer to the ERC4626 vault before any user's `DirectDepositV1` is processed is sufficient. The cost of the attack is the donated tokens, but the attacker does not lose them permanently: they can redeem their existing vault shares at the inflated price and recover most of the donated amount. The attack is therefore economically viable against any user whose deposit amount is small relative to the inflated `totalAssets`. Products backed by ERC4626 vaults with low initial liquidity are especially susceptible.

---

### Recommendation

1. **Remove the `previewDeposit != 0` silent-skip guard.** If the vault cannot accept the deposit (e.g. zero assets), the call should revert rather than silently succeed.
2. **Add a minimum-shares slippage parameter** to `wrapVaultAsset` (analogous to `amountMin` in the referenced report). The caller or keeper should supply the minimum acceptable shares, and the function should revert if `previewDeposit(assetBalance) < minShares`.
3. **Validate the return value of `IERC4626Base.deposit`.** The actual shares minted (returned by `deposit`) should be checked to be `>= minShares` after the call.
4. **Consider virtual shares/assets** (ERC4626 inflation-resistance pattern, e.g. OpenZeppelin's `_decimalsOffset`) in any vault whose token is accepted as Nado collateral.

---

### Proof of Concept

**Setup:**
- ERC4626 vault `V` has `totalSupply = 1000 shares`, `totalAssets = 1000 USDC`.
- User `Alice` sends `500 USDC` to her `DirectDepositV1` address `DDA_Alice`.

**Attack:**
1. Attacker calls `V.transfer(address(V), 500_001 USDC)` — donating directly to the vault, bypassing `deposit`. Now `totalAssets = 501_001`, `totalSupply = 1000`.
2. `previewDeposit(500) = 500 * 1000 / 501_001 = 0` (integer truncation).

**Execution:**
3. Keeper calls `isWrapVaultAssetReady(DDA_Alice, productId, false)` → `previewDeposit(500) == 0` → returns `false`. Keeper skips Alice.
4. Even if `wrapVaultAsset(aliceSubaccount, productId)` is called directly, line 525 evaluates `0 != 0` → `false`, the `if` body is skipped, function returns with no state change.

**Result:** Alice's 500 USDC remain in `DDA_Alice` indefinitely. Her subaccount receives no collateral credit. The attacker can redeem their 1000 shares for `1000 * 501_001 / 1000 ≈ 501_001 USDC` (recovering the donated amount plus Alice's locked funds proportionally once any legitimate deposit eventually goes through).

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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

**File:** core/contracts/ContractOwner.sol (L547-561)
```text
        uint256 assetBalance = IERC20Base(assetTokenAddr).balanceOf(recipient);
        uint256 wrappedBalance = IERC4626Base(tokenAddr).previewDeposit(
            assetBalance
        );
        if (wrappedBalance != 0) {
            wrappedBalance *= 10**(18 - IERC20Base(tokenAddr).decimals());

            return
                _isDepositAmountReady(
                    productId,
                    wrappedBalance,
                    isFirstDeposit
                );
        }
        return false;
```

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
