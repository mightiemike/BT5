### Title
Permissionless `wrapVaultAsset` Allows Any Caller to Force-Convert a Victim's DDA Underlying Tokens to Vault Shares, Altering Collateral Type and Health Profile Without Consent — (`core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.wrapVaultAsset` carries no access-control modifier. Any external caller can invoke it for an arbitrary `subaccount`, pulling the underlying tokens out of the victim's Direct Deposit Address (DDA), depositing them into an ERC-4626 vault, and returning vault shares to the DDA. A subsequent permissionless call to `creditDepositV1` then credits those vault shares — not the original underlying — to the victim's subaccount. Because vault-share products carry their own `longWeightInitialX18` / `longWeightMaintenanceX18` risk parameters and oracle price, the victim's health factor is silently degraded without their knowledge or consent.

---

### Finding Description

`wrapVaultAsset` is declared `external` with no `onlyOwner` or caller-identity guard: [1](#0-0) 

The function:
1. Resolves the DDA for the supplied `subaccount`.
2. Fetches the vault token address for `productId` from `spotEngine`.
3. Reads the DDA's balance of the vault's underlying asset.
4. Calls `DirectDepositV1.withdraw(assetToken)` — which transfers the underlying from the DDA to `ContractOwner` (valid because `ContractOwner` is the DDA's `Ownable` owner).
5. Approves the vault and calls `IERC4626Base.deposit(assetBalance, directDepositV1)`, sending vault shares back to the DDA. [2](#0-1) 

`creditDepositV1` is equally permissionless: [3](#0-2) 

`creditDeposit` inside the DDA iterates all registered spot products and deposits whatever token balance it finds: [4](#0-3) 

So the full attacker-controlled sequence is:
```
attacker → wrapVaultAsset(victimSubaccount, vaultProductId)
         → creditDepositV1(victimSubaccount)
```
After step 1 the DDA holds vault shares; after step 2 those shares are credited to the victim's subaccount under the vault product's risk parameters.

---

### Impact Explanation

Health is computed per-product using `longWeightInitialX18` / `longWeightMaintenanceX18` and the product's oracle `priceX18`: [5](#0-4) [6](#0-5) 

If the vault product is registered with a lower `longWeightInitialX18` than the underlying spot product, the victim's initial health contribution from that collateral decreases proportionally. Concretely:

- Victim deposits 10,000 USDC to their DDA (underlying product weight = 1.0).
- Attacker calls `wrapVaultAsset` → `creditDepositV1`; victim now holds vault shares (weight = 0.85, for example).
- Victim's initial health from that collateral drops from 10,000 to 8,500 USD equivalent.
- If the victim already has open perp positions sized against the 10,000 USD collateral, they may now be below initial margin and subject to liquidation.

Additionally, vault shares may carry withdrawal restrictions or redemption delays not present in the underlying, locking the victim's collateral.

---

### Likelihood Explanation

- The attack requires no special privileges — `wrapVaultAsset` is a plain `external` function callable by any EOA or contract.
- The only precondition is that (a) the victim's DDA holds underlying tokens and (b) a vault product whose `asset()` matches that underlying is registered in `spotEngine`. Both are normal operational states.
- The attacker pays only gas.

---

### Recommendation

Add a caller-identity check so only the subaccount owner (or the protocol owner) can trigger wrapping for a given subaccount. The simplest fix is to restrict `wrapVaultAsset` to `onlyOwner`, consistent with other sensitive DDA operations such as `withdrawFromDirectDepositV1`: [7](#0-6) 

Alternatively, derive the expected subaccount owner from the `subaccount` bytes32 and require `msg.sender` to match, mirroring the pattern used elsewhere in the protocol for user-initiated actions.

---

### Proof of Concept

```solidity
// Preconditions:
// - vaultProductId is registered in spotEngine; vault.asset() == USDC
// - vault product has longWeightInitialX18 < USDC product's longWeightInitialX18
// - victim's DDA holds 10_000e6 USDC (sent by victim, not yet credited)

contractOwner.wrapVaultAsset(victimSubaccount, vaultProductId);
// DDA now holds vault shares instead of USDC

contractOwner.creditDepositV1(victimSubaccount);
// Vault shares credited to victim's subaccount under vault product risk params

// Assert: victim's initial health < health they would have had with USDC credited
// Assert: victim may now be liquidatable
```

### Citations

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

**File:** core/contracts/ContractOwner.sol (L622-624)
```text
    function withdrawFromDirectDepositV1(bytes32 subaccount, address token)
        external
        onlyOwner
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
