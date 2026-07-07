Let me check the access control on `wrapVaultAsset` and the `creditDeposit` flow more carefully, and also look at how collateral weights are applied.

Based on my full trace of the code, I have a definitive answer.

---

### Title
Missing Access Control on `wrapVaultAsset` Allows Any EOA to Silently Convert a User's Pending Underlying Deposit to Vault Shares at Different Collateral Weights — (`core/contracts/ContractOwner.sol`)

### Summary

`wrapVaultAsset` carries no access-control modifier. Any EOA can invoke it for any subaccount. When the vault's underlying asset is also a registered spot product, the call converts the DDA's pending underlying balance into vault shares before `creditDepositV1` runs, causing the subaccount to receive credit under the vault product instead of the underlying product. Because the two products carry independent `longWeightInitial` / `longWeightMaintenance` values, the subaccount's health changes without its owner's consent.

### Finding Description

`wrapVaultAsset` is declared `external` with no `onlyOwner`, `onlyDeployer`, or any other guard: [1](#0-0) 

The function:
1. Reads `assetTokenAddr` from `IERC4626Base(tokenAddr).asset()`.
2. Calls `DirectDepositV1.withdraw(assetTokenAddr)` — which is `onlyOwner` on the DDA, and `ContractOwner` is the DDA's owner — pulling the entire underlying balance from the DDA to `ContractOwner`.
3. Approves the vault and calls `vault.deposit(assetBalance, directDepositV1)`, minting vault shares back to the DDA. [2](#0-1) 

`creditDeposit` then iterates every registered spot product and deposits whatever balance the DDA holds for each: [3](#0-2) 

If the underlying token is also a registered spot product (e.g., USDC at productId=1 and a USDC vault share at productId=5), after `wrapVaultAsset` the DDA holds 0 USDC and N vault shares. `creditDeposit` deposits 0 under productId=1 and N under productId=5.

Health is computed per-product using independent `longWeightInitialX18` / `longWeightMaintenanceX18`: [4](#0-3) [5](#0-4) 

Vault shares routinely carry lower collateral weights than their underlying. The subaccount's initial and maintenance health both decrease by `amount × price × (weightUnderlying − weightVault)`.

### Impact Explanation

- Subaccount loses underlying-asset credit and gains vault-share credit at a lower collateral weight.
- Initial health drops → subaccount may be blocked from opening new positions.
- Maintenance health drops → subaccount may become liquidatable.
- The attacker needs no capital and no special role; the only cost is gas.

### Likelihood Explanation

The precondition (both underlying and vault token registered as spot products) is a realistic and documented deployment pattern for yield-bearing collateral. The attack is a single permissionless call, executable atomically or as a front-run of `creditDepositV1`. No admin compromise, leaked key, or governance capture is required.

### Recommendation

Add an access-control modifier to `wrapVaultAsset`. The simplest fix is `onlyOwner` (multisig), consistent with other privileged operations in `ContractOwner`. Alternatively, restrict callers to the subaccount owner derived from the `subaccount` bytes32 (i.e., `require(address(bytes20(subaccount)) == msg.sender)`).

### Proof of Concept

```
Setup:
  - Register USDC as spot productId=1 (longWeightInitial=0.9e18)
  - Register aUSDC vault as spot productId=5 (longWeightInitial=0.85e18, asset()=USDC)
  - User sends 1000 USDC to their DDA

Attack (single tx from attacker EOA):
  1. attacker.call → ContractOwner.wrapVaultAsset(userSubaccount, 5)
     → DDA.withdraw(USDC) → 1000 USDC moves to ContractOwner
     → vault.deposit(1000, DDA) → DDA receives ~1000 aUSDC shares

  2. (anyone) ContractOwner.creditDepositV1(userSubaccount)
     → creditDeposit iterates products
     → productId=1 (USDC): balance=0, skipped
     → productId=5 (aUSDC): balance=1000, deposited

Assert:
  - subaccount.balance[productId=1] == 0   (expected: 1000 USDC)
  - subaccount.balance[productId=5] == 1000 aUSDC
  - health = 1000 × price × 0.85 instead of 1000 × price × 0.90
  - health delta = −1000 × price × 0.05 (negative, attacker-controlled)
```

### Citations

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

**File:** core/contracts/DirectDepositV1.sol (L103-106)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }
```

**File:** core/contracts/BaseEngine.sol (L157-177)
```text
    function _calculateProductHealth(
        uint32 productId,
        bytes32 subaccount,
        IProductEngine.HealthType healthType
    ) internal returns (int128 health) {
        RiskHelper.Risk memory risk = _risk(productId);
        (int128 amount, int128 quoteAmount) = _getBalance(
            productId,
            subaccount
        );
        int128 weight = RiskHelper._getWeightX18(risk, amount, healthType);
        health += quoteAmount;

        if (amount != 0) {
            if (weight == 2 * ONE) {
                return -INF;
            }
            health += amount.mul(weight).mul(risk.priceX18);
            emit PriceQuery(productId);
        }
    }
```

**File:** core/contracts/libraries/RiskHelper.sol (L34-55)
```text
    function _getWeightX18(
        Risk memory risk,
        int128 amount,
        IProductEngine.HealthType healthType
    ) internal pure returns (int128) {
        if (healthType == IProductEngine.HealthType.PNL) {
            return ONE;
        }

        int128 weight;
        if (amount >= 0) {
            weight = healthType == IProductEngine.HealthType.INITIAL
                ? risk.longWeightInitialX18
                : risk.longWeightMaintenanceX18;
        } else {
            weight = healthType == IProductEngine.HealthType.INITIAL
                ? risk.shortWeightInitialX18
                : risk.shortWeightMaintenanceX18;
        }

        return weight;
    }
```
