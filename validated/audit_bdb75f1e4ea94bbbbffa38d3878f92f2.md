### Title
Fee-on-Transfer Token Causes Deposit DoS and Clearinghouse Accounting Inflation — (`File: core/contracts/EndpointStorage.sol`)

---

### Summary

`EndpointStorage.handleDepositTransfer()` performs two sequential token transfers using the caller-supplied `amount` for both legs. When the underlying collateral token charges a fee on transfer, the Endpoint receives fewer tokens than `amount` from the user, then immediately attempts to forward the full `amount` to the Clearinghouse — causing a revert. Even if both transfers succeeded, `Clearinghouse.depositCollateral()` credits the full `txn.amount` to the user's spot balance rather than the actual received amount, inflating internal accounting beyond real holdings.

---

### Finding Description

The deposit entry path is:

1. User calls `Endpoint.depositCollateralWithReferral()` [1](#0-0) 
2. Which calls `handleDepositTransfer(token, msg.sender, uint256(amount))` [2](#0-1) 
3. `handleDepositTransfer` executes two transfers back-to-back, both using the same `amount`: [3](#0-2) 

```solidity
function handleDepositTransfer(
    IERC20Base token,
    address from,
    uint256 amount
) internal {
    require(address(token) != address(0), ERR_INVALID_PRODUCT);
    safeTransferFrom(token, from, amount);                    // user → Endpoint
    safeTransferTo(token, address(clearinghouse), amount);    // Endpoint → Clearinghouse
}
```

With a fee-on-transfer token (e.g., USDT with fees enabled):
- `safeTransferFrom(token, from, amount)` → Endpoint receives `amount - fee`
- `safeTransferTo(token, clearinghouse, amount)` → Endpoint tries to send `amount` but only holds `amount - fee` → **revert**

This makes all deposits for that product permanently unavailable to any user.

If the Endpoint happened to hold a pre-existing balance of the token (e.g., from a slow-mode fee payment), the second transfer could succeed, but the Clearinghouse would receive `amount - fee`. The sequencer then processes the queued `DepositCollateral` transaction, which credits the full original `txn.amount` to the user's spot balance: [4](#0-3) 

```solidity
int128 amountRealized = int128(txn.amount) * int128(multiplier);
spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
```

The `txn.amount` is the original user-supplied value, not the actual received amount. The Clearinghouse's real token balance is therefore less than the sum of all credited spot balances, creating a solvency gap that grows with each deposit.

---

### Impact Explanation

**Primary impact — Deposit DoS:** Any user attempting to deposit a fee-on-transfer collateral token will have their transaction revert at the second `safeTransfer` leg inside `handleDepositTransfer`. The entire deposit flow for that product becomes unavailable to all users. [5](#0-4) 

**Secondary impact — Solvency/accounting corruption:** If the double-transfer succeeds (Endpoint has residual balance), the Clearinghouse is credited with fewer tokens than the user's spot balance reflects. Over time, the last users to withdraw will find the Clearinghouse insolvent for that product — they cannot withdraw their full credited balance because the real token reserve is insufficient. [4](#0-3) 

---

### Likelihood Explanation

Medium. The protocol must list a fee-on-transfer token as a supported collateral product. USDT has a fee switch that can be enabled by its issuer. If such a token is listed, the impact is immediate and affects all depositors of that product without any special attacker action — a normal deposit call is sufficient to trigger the revert.

---

### Recommendation

Measure the actual received amount by comparing `balanceOf` before and after the `safeTransferFrom`, and use that delta for both the forwarding transfer and the credited amount:

```solidity
function handleDepositTransfer(
    IERC20Base token,
    address from,
    uint256 amount
) internal {
    require(address(token) != address(0), ERR_INVALID_PRODUCT);
    uint256 before = token.balanceOf(address(this));
    safeTransferFrom(token, from, amount);
    uint256 received = token.balanceOf(address(this)) - before;
    safeTransferTo(token, address(clearinghouse), received);
    // The SlowModeTx must encode `received`, not `amount`
}
```

The `DepositCollateral` slow-mode transaction queued in `depositCollateralWithReferral` must also encode the actual received amount so that `Clearinghouse.depositCollateral` credits the correct value. Alternatively, restrict supported collateral tokens to non-fee-on-transfer tokens via an explicit allowlist check at product registration time.

---

### Proof of Concept

1. A fee-on-transfer token (e.g., USDT with 1% fee enabled) is registered as a spot collateral product.
2. Alice calls `Endpoint.depositCollateral(subaccountName, productId, 1000e6)`.
3. `handleDepositTransfer` is invoked with `amount = 1000e6`. [3](#0-2) 
4. `safeTransferFrom(token, Alice, 1000e6)` succeeds; Endpoint receives `990e6` (fee deducted).
5. `safeTransferTo(token, clearinghouse, 1000e6)` attempts to send `1000e6` but Endpoint only holds `990e6` → **transaction reverts**.
6. Alice's deposit fails. Every subsequent deposit attempt by any user for this product also reverts.
7. The product is effectively bricked for all depositors without any privileged action.

### Citations

**File:** core/contracts/Endpoint.sol (L123-148)
```text
    function depositCollateralWithReferral(
        bytes32 subaccount,
        uint32 productId,
        uint128 amount,
        string memory
    ) public {
        require(!RiskHelper.isIsolatedSubaccount(subaccount), ERR_UNAUTHORIZED);

        address sender = address(bytes20(subaccount));

        // depositor / depositee need to be unsanctioned
        requireUnsanctioned(msg.sender);
        requireUnsanctioned(sender);

        if (!isValidDepositAmount(subaccount, productId, amount)) {
            // we cannot revert here, otherwise direct deposit could be blocked when there are
            // multiple assets awaiting credit but one of them is below the minimum deposit amount.
            // we can just skip the deposit and continue with the next asset.
            return;
        }

        handleDepositTransfer(
            IERC20Base(spotEngine.getToken(productId)),
            msg.sender,
            uint256(amount)
        );
```

**File:** core/contracts/EndpointStorage.sol (L111-119)
```text
    function handleDepositTransfer(
        IERC20Base token,
        address from,
        uint256 amount
    ) internal {
        require(address(token) != address(0), ERR_INVALID_PRODUCT);
        safeTransferFrom(token, from, amount);
        safeTransferTo(token, address(clearinghouse), amount);
    }
```

**File:** core/contracts/Clearinghouse.sol (L204-208)
```text
        int256 multiplier = int256(10**(MAX_DECIMALS - decimals));
        int128 amountRealized = int128(txn.amount) * int128(multiplier);

        spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
        emit ModifyCollateral(amountRealized, txn.sender, txn.productId);
```
