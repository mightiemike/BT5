### Title
`DirectDepositV1::creditDeposit` Approves Non-Zero Allowance Without Prior Reset, Permanently Bricking Deposits for USDT-Like Tokens — (`File: core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1::creditDeposit()` calls `token.approve(address(endpoint), balance)` directly without first resetting the allowance to zero. For tokens like USDT that revert when changing a non-zero allowance to another non-zero value, a residual allowance left by a prior partial deposit (caused by `uint256`→`uint128` truncation) permanently bricks all future `creditDeposit()` calls for that token in that DDA instance.

---

### Finding Description

In `creditDeposit()`, the balance is read as `uint256` and the full `uint256` value is approved, but only `uint128(balance)` is passed to `depositCollateralWithReferral`:

```solidity
// DirectDepositV1.sol lines 90–98
uint256 balance = token.balanceOf(address(this));
if (balance != 0) {
    token.approve(address(endpoint), balance);          // approves full uint256
    endpoint.depositCollateralWithReferral(
        subaccount,
        productId,
        uint128(balance),                               // deposits only uint128 truncation
        "-1"
    );
}
```

If `balance > type(uint128).max`, the endpoint pulls only `uint128(balance)` tokens. The remaining allowance is `balance - uint128(balance)` — a non-zero residual. On the next invocation of `creditDeposit()`, the code attempts `token.approve(address(endpoint), balance2)` where `balance2 > 0`, changing a non-zero allowance to another non-zero value. USDT-like tokens revert on this operation, permanently blocking all future deposits for that token through this DDA.

Notably, `ContractOwner::wrapVaultAsset()` in the same codebase already applies the correct two-step pattern (`approve(tokenAddr, 0)` then `approve(tokenAddr, assetBalance)`), confirming the team is aware of the requirement — but `creditDeposit()` was not updated consistently.

---

### Impact Explanation

Once a residual allowance is established, every subsequent call to `creditDeposit()` for that token reverts. User funds (USDT or any USDT-like collateral) sent to the DDA accumulate but cannot be deposited as collateral. The only recovery path is the owner calling `withdraw()` to rescue the tokens — but this requires privileged intervention and breaks the permissionless deposit flow the DDA is designed to provide. The corrupted state is: `allowance[DDA][endpoint][token] > 0` persisting across calls, causing all future `approve` calls to revert.

---

### Likelihood Explanation

The trigger requires `balance > type(uint128).max` in the DDA for a given token. For USDT (6 decimals) this threshold is ~3.4 × 10^32 USDT — practically unreachable under normal conditions. However, the code defect is structurally present and the `uint256`→`uint128` truncation is a concrete, code-level mismatch (not a theoretical assumption). The risk is elevated if the protocol lists tokens with 18 decimals and lower unit value, where the threshold is ~3.4 × 10^20 tokens. The `creditDeposit()` function is `external` with no access control, so any caller can trigger it once the residual state exists.

---

### Recommendation

Apply the same two-step approval pattern already used in `ContractOwner::wrapVaultAsset()`:

```solidity
// DirectDepositV1.sol creditDeposit(), inside the if (balance != 0) block
token.approve(address(endpoint), 0);
token.approve(address(endpoint), balance);
```

Additionally, the `uint256`→`uint128` truncation at line 96 should be guarded with `require(balance <= type(uint128).max)` or the balance should be capped to `type(uint128).max` before both the `approve` and the deposit call, so the approved amount and deposited amount are always identical.

---

### Proof of Concept

1. A USDT-like token is listed as a spot product in `SpotEngine`.
2. A DDA is created for a subaccount via `ContractOwner::createDirectDepositV1`.
3. A deposit of `balance > type(uint128).max` accumulates in the DDA (e.g., via direct transfer).
4. Anyone calls `DirectDepositV1::creditDeposit()`:
   - `token.approve(endpoint, balance)` — sets allowance to `balance` (uint256).
   - `endpoint.depositCollateralWithReferral(..., uint128(balance), ...)` — endpoint pulls only `uint128(balance)`, leaving residual allowance = `balance - uint128(balance) > 0`.
5. On any subsequent call to `creditDeposit()` with any non-zero USDT balance:
   - `token.approve(endpoint, balance2)` — attempts to change non-zero allowance to non-zero → **USDT reverts**.
   - All future deposits for this token in this DDA are permanently bricked. [1](#0-0) [2](#0-1)

### Citations

**File:** core/contracts/DirectDepositV1.sol (L90-99)
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
            }
```

**File:** core/contracts/ContractOwner.sol (L529-531)
```text
            IERC20Base assetToken = IERC20Base(assetTokenAddr);
            assetToken.approve(tokenAddr, 0);
            assetToken.approve(tokenAddr, assetBalance);
```
