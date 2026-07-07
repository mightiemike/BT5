### Title
Wrapped Native Tokens Permanently Stranded in `DirectDepositV1` When Deposit Falls Below Minimum Threshold — (`File: core/contracts/DirectDepositV1.sol`)

---

### Summary

When a user sends native ETH to their `DirectDepositV1` (DDA) contract, the ETH is immediately and irrevocably wrapped to WETH by the `receive()` function. If the resulting WETH balance is below the minimum deposit threshold enforced by `Endpoint.depositCollateralWithReferral`, the deposit is silently skipped and the WETH remains permanently stranded in the DDA. The user's subaccount is never credited, and the user has no mechanism to recover the stranded WETH since `withdraw()` is restricted to the DDA owner (`ContractOwner`), not the original depositor.

---

### Finding Description

**Step 1 — ETH is irrevocably wrapped on receipt.**

The `receive()` function in `DirectDepositV1.sol` immediately wraps all incoming ETH to WETH by calling the wrapped-native contract: [1](#0-0) 

There is no minimum-amount check here. Any ETH sent — regardless of size — is wrapped to WETH and held in the DDA.

**Step 2 — `creditDeposit()` attempts to forward the WETH to the Endpoint.**

`creditDeposit()` is a permissionless function that reads the DDA's token balances and calls `endpoint.depositCollateralWithReferral` for each non-zero balance: [2](#0-1) 

**Step 3 — `depositCollateralWithReferral` silently returns without transferring tokens when the amount is below the minimum.**

Inside `Endpoint.sol`, `depositCollateralWithReferral` contains an explicit silent-return path: [3](#0-2) 

The comment confirms this is intentional for the multi-asset DDA flow. When the WETH balance is below `MIN_DEPOSIT_AMOUNT` (or the higher `MIN_FIRST_DEPOSIT_AMOUNT` for a new subaccount), the function returns without calling `handleDepositTransfer`. No tokens move. No slow-mode transaction is queued. The user's subaccount is never credited. [4](#0-3) 

**Step 4 — The user cannot recover the stranded WETH.**

The only recovery path for tokens in the DDA is `withdraw()`, which is `onlyOwner`: [5](#0-4) 

The DDA is deployed by `ContractOwner` (which calls `createDirectDepositV1`), making `ContractOwner` the Ownable owner — not the user who sent the ETH. The user has no direct path to recover their stranded WETH. [6](#0-5) 

---

### Impact Explanation

A user who sends native ETH to their DDA in an amount below the minimum deposit threshold loses that ETH permanently from their perspective. The ETH is consumed (wrapped to WETH), the DDA holds the WETH, but the user's on-chain subaccount balance is never updated. The user cannot call `withdraw()` to reclaim the WETH. The broken invariant is: *ETH sent to a DDA must either be credited to the user's subaccount or be recoverable by the user.* This invariant is violated for any sub-minimum deposit.

---

### Likelihood Explanation

The scenario is reachable by any unprivileged user interacting with their DDA. It is especially likely for first-time depositors, because `MIN_FIRST_DEPOSIT_AMOUNT` is higher than `MIN_DEPOSIT_AMOUNT`, widening the range of amounts that trigger the silent skip. A user testing with a small ETH amount, or a user whose ETH value drops below the minimum between the time they send and the time `creditDeposit()` is called, will silently lose funds with no on-chain error or event to indicate what happened.

---

### Recommendation

1. **Add a minimum-amount guard in `receive()`** or in `creditDeposit()` before wrapping/depositing. If the amount does not meet the minimum, either revert the `receive()` call (so the user's ETH is returned) or hold the ETH unwrapped and allow the user to reclaim it.
2. **Expose a user-callable recovery function** on the DDA (or on `ContractOwner`) that allows the subaccount owner — not just the DDA deployer — to withdraw stranded tokens back to their wallet.
3. **Emit an event** in `depositCollateralWithReferral` when a deposit is silently skipped, so off-chain monitoring can detect and alert on stranded funds.

---

### Proof of Concept

1. Alice's DDA is deployed by `ContractOwner`; Alice's subaccount has never deposited before, so `MIN_FIRST_DEPOSIT_AMOUNT` applies.
2. Alice sends 0.001 ETH to her DDA address.
3. `receive()` fires: `wrappedNative.call{value: 0.001 ether}("")` wraps the ETH; the DDA now holds 0.001 WETH.
4. Alice (or anyone) calls `creditDeposit()`.
5. `creditDeposit()` reads `balance = 0.001 WETH`, approves the Endpoint, and calls `endpoint.depositCollateralWithReferral(aliceSubaccount, wethProductId, 0.001e18, "-1")`.
6. Inside `depositCollateralWithReferral`, `isValidDepositAmount` returns `false` (0.001 WETH < `MIN_FIRST_DEPOSIT_AMOUNT`).
7. The function silently returns. `handleDepositTransfer` is never called. No slow-mode tx is queued.
8. Alice's subaccount balance is unchanged. The 0.001 WETH remains in the DDA.
9. Alice has no `withdraw()` access (she is not the DDA owner). Her ETH is permanently inaccessible to her.

### Citations

**File:** core/contracts/DirectDepositV1.sol (L64-67)
```text
    receive() external payable {
        (bool success, ) = wrappedNative.call{value: msg.value}("");
        require(success, "Failed to wrap native token.");
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

**File:** core/contracts/Endpoint.sol (L90-101)
```text
    function isValidDepositAmount(
        bytes32 subaccount,
        uint32 productId,
        uint128 amount
    ) internal returns (bool) {
        int256 minDepositAmount = MIN_DEPOSIT_AMOUNT;
        if (subaccount != X_ACCOUNT && (subaccountIds[subaccount] == 0)) {
            minDepositAmount = MIN_FIRST_DEPOSIT_AMOUNT;
        }
        return
            clearinghouse.checkMinDeposit(productId, amount, minDepositAmount);
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

**File:** core/contracts/ContractOwner.sol (L511-514)
```text
        address payable directDepositV1 = directDepositV1Address[subaccount];
        if (directDepositV1 == address(0)) {
            directDepositV1 = createDirectDepositV1(subaccount);
        }
```
