### Title
Bare `approve()` Without Allowance Reset Permanently Bricks USDT Deposits in `DirectDepositV1` — (File: `core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` calls `token.approve(address(endpoint), balance)` directly without first resetting the allowance to zero. USDT reverts on any `approve()` call where the current allowance is non-zero. Once a residual allowance is left (via the `uint128` truncation path described below), every subsequent call to `creditDeposit()` for USDT will revert, permanently bricking USDT deposits through the Direct Deposit Account (DDA) mechanism. Notably, `ContractOwner.wrapVaultAsset()` already applies the correct two-step pattern (`approve(0)` → `approve(amount)`), confirming the developers are aware of the requirement but missed it in `creditDeposit()`.

---

### Finding Description

`DirectDepositV1.creditDeposit()` iterates over all registered spot product IDs and, for each token with a non-zero balance, calls:

```solidity
token.approve(address(endpoint), balance);          // balance is uint256
endpoint.depositCollateralWithReferral(
    subaccount,
    productId,
    uint128(balance),   // ← truncated to uint128
    "-1"
);
```

The approve is for the full `uint256 balance`, but the deposit call passes only `uint128(balance)`. If `balance > type(uint128).max`, the endpoint's internal `transferFrom` consumes only `uint128(balance)` tokens, leaving a residual allowance of `balance - uint128(balance)` on the DDA contract.

On the next invocation of `creditDeposit()`, the line `token.approve(address(endpoint), balance)` will revert for USDT because USDT's `approve()` requires the existing allowance to be zero before a new non-zero value is set.

The function has **no access control** — any unprivileged caller can invoke it via `ContractOwner.creditDepositV1(subaccount)`, which also has no access restriction.

By contrast, `ContractOwner.wrapVaultAsset()` correctly uses:

```solidity
assetToken.approve(tokenAddr, 0);
assetToken.approve(tokenAddr, assetBalance);
```

This inconsistency confirms the two-step pattern was known and intentionally applied elsewhere, but omitted in `creditDeposit()`.

---

### Impact Explanation

Once a non-zero USDT allowance is left on a DDA contract, every future call to `creditDeposit()` for that DDA will revert at the `approve` line. USDT deposited to the DDA address will be permanently stuck — it cannot be credited to the subaccount via the normal flow. The `withdraw()` function is `onlyOwner`, so only the DDA owner can rescue funds, but the deposit path is permanently broken for that contract instance.

---

### Likelihood Explanation

The `uint128` truncation trigger requires `balance > type(uint128).max` (~3.4 × 10³² USDT at 6 decimals), which is practically impossible under normal conditions. However:

1. The structural absence of the allowance reset is a real code defect, inconsistent with the pattern used in `wrapVaultAsset()`.
2. If the endpoint's `depositCollateralWithReferral` internally rounds down or caps the transferred amount for any reason (not fully analyzed due to scope), a residual allowance could arise at realistic balances.
3. The function is callable by any unprivileged user, meaning no privileged access is needed to trigger the broken state once conditions are met.

Likelihood is **low-medium**: the `uint128` truncation path is the only confirmed trigger and requires an extreme balance, but the structural defect is real and the endpoint's full consumption guarantee was not verified.

---

### Recommendation

Apply the two-step allowance reset pattern in `creditDeposit()`, consistent with `wrapVaultAsset()`:

```solidity
token.approve(address(endpoint), 0);
token.approve(address(endpoint), balance);
```

Alternatively, use OpenZeppelin's `SafeERC20.forceApprove()` which handles this atomically.

---

### Proof of Concept

**Root cause — bare approve without reset:** [1](#0-0) 

**Truncation mismatch — approve for `uint256 balance`, deposit for `uint128(balance)`:** [2](#0-1) 

**Correct two-step pattern used in `wrapVaultAsset()` (same codebase):** [3](#0-2) 

**No access control on `creditDepositV1` — any caller can trigger:** [4](#0-3)

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

**File:** core/contracts/ContractOwner.sol (L530-532)
```text
            assetToken.approve(tokenAddr, 0);
            assetToken.approve(tokenAddr, assetBalance);
            IERC4626Base(tokenAddr).deposit(assetBalance, directDepositV1);
```
