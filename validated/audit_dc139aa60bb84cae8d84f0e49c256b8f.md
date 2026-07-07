### Title
Stuck Non-Zero Allowance in `creditDeposit()` Permanently Blocks Token Deposits for DirectDepositV1 Subaccounts — (File: `core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` calls `token.approve(address(endpoint), balance)` without first resetting the allowance to zero. When `Endpoint.depositCollateralWithReferral()` silently skips a deposit because the amount is below the minimum threshold, the allowance is set but never consumed. Any subsequent call to `creditDeposit()` for a USDT-style token (which requires allowance to be zero before a new approval) will revert, permanently blocking the deposit flow for that DirectDepositV1 instance.

---

### Finding Description

`DirectDepositV1.creditDeposit()` iterates over all spot product tokens and, for each token with a non-zero balance, calls:

```solidity
token.approve(address(endpoint), balance);
endpoint.depositCollateralWithReferral(subaccount, productId, uint128(balance), "-1");
``` [1](#0-0) 

Inside `Endpoint.depositCollateralWithReferral()`, there is an explicit early-return path when the deposit amount is below the minimum:

```solidity
if (!isValidDepositAmount(subaccount, productId, amount)) {
    // we cannot revert here, otherwise direct deposit could be blocked...
    return;
}
``` [2](#0-1) 

When this early return fires, `handleDepositTransfer()` is never called, so the token allowance granted at line 92 of `DirectDepositV1.sol` is **not consumed** — it remains at `balance` (non-zero). On the next invocation of `creditDeposit()`, the call to `token.approve(address(endpoint), newBalance)` will revert for any token whose `approve()` implementation requires the current allowance to be zero before setting a new value (e.g., USDT on mainnet/Ethereum L2s). This permanently breaks the deposit flow for that DirectDepositV1 instance and that token.

`creditDeposit()` has **no access control** — it is callable by any external account: [3](#0-2) 

`ContractOwner.creditDepositV1()` is also callable by anyone and routes directly into `creditDeposit()`: [4](#0-3) 

---

### Impact Explanation

Once the allowance is stuck at a non-zero value, every subsequent call to `creditDeposit()` reverts for the affected token. Funds deposited to the DirectDepositV1 address cannot be credited to the subaccount through the normal deposit flow. The owner can call `withdraw()` to recover tokens, but the deposit mechanism is permanently disabled for that DirectDepositV1 instance and token combination — there is no function in `DirectDepositV1` to reset the allowance. Because `createDirectDepositV1` uses a hardcoded `salt: bytes32(uint256(1))`, redeployment of a fresh instance is also blocked. [5](#0-4) 

**Impact: Medium** — deposit flow permanently broken for affected subaccount/token pair; funds recoverable only via manual owner withdrawal, not via the intended deposit path.

---

### Likelihood Explanation

The trigger requires two conditions: (1) a USDT-style token is listed as a spot product, and (2) a dust-amount deposit below `MIN_DEPOSIT_AMOUNT` or `MIN_FIRST_DEPOSIT_AMOUNT` reaches the DirectDepositV1 address before a legitimate deposit. An unprivileged attacker can deliberately send a dust amount to any DirectDepositV1 address and then call `creditDeposit()` to set the stuck allowance. No privileged access is required.

**Likelihood: Medium** — requires a specific token type and a deliberate or accidental dust deposit, both realistic on a live deployment.

---

### Recommendation

Reset the allowance to zero before setting a new one inside `creditDeposit()`:

```solidity
token.approve(address(endpoint), 0);
token.approve(address(endpoint), balance);
```

Alternatively, use OpenZeppelin's `SafeERC20.forceApprove()` (or `safeIncreaseAllowance` / `safeDecreaseAllowance`) to handle non-standard ERC-20 approve semantics safely.

---

### Proof of Concept

1. A USDT-style token is registered as a spot product in `SpotEngine`.
2. Attacker sends 1 wei of USDT to the DirectDepositV1 address for a target subaccount.
3. Attacker (or anyone) calls `ContractOwner.creditDepositV1(subaccount)` → `DirectDepositV1.creditDeposit()`.
4. `token.approve(address(endpoint), 1)` succeeds — allowance is now 1.
5. `endpoint.depositCollateralWithReferral(...)` is called with `amount = 1`, which is below `MIN_DEPOSIT_AMOUNT` / `MIN_FIRST_DEPOSIT_AMOUNT`, so it returns early without transferring tokens. Allowance remains at 1.
6. A legitimate user sends 1000 USDT to the same DirectDepositV1 address.
7. Anyone calls `creditDepositV1(subaccount)` again.
8. `token.approve(address(endpoint), 1000)` **reverts** because USDT's `approve()` requires current allowance to be 0.
9. The entire `creditDeposit()` call reverts. The 1000 USDT is stuck in the DirectDepositV1 contract and cannot be deposited via the normal flow. [6](#0-5) [2](#0-1)

### Citations

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

**File:** core/contracts/Endpoint.sol (L137-142)
```text
        if (!isValidDepositAmount(subaccount, productId, amount)) {
            // we cannot revert here, otherwise direct deposit could be blocked when there are
            // multiple assets awaiting credit but one of them is below the minimum deposit amount.
            // we can just skip the deposit and continue with the next asset.
            return;
        }
```

**File:** core/contracts/ContractOwner.sol (L495-497)
```text
        DirectDepositV1 directDepositV1 = new DirectDepositV1{
            salt: bytes32(uint256(1))
        }(address(endpoint), address(spotEngine), subaccount, wrappedNative);
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
