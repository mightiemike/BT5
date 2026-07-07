### Title
Silent Token Stranding in `creditDeposit()` When Deposit Amount Falls Below Minimum — (`File: core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` follows the same approve-then-call pattern as the LiFi `_executeWithToken()` bug. It approves `endpoint` for the full token balance, then calls `endpoint.depositCollateralWithReferral()`. That function contains an explicit early-return path that skips the `transferFrom` pull when the deposit amount is below the protocol minimum. When this happens, the tokens remain stranded inside `DirectDepositV1` with a live approval to `endpoint`, and the depositing user has no on-chain path to recover them — only the contract owner (protocol admin) can.

---

### Finding Description

`DirectDepositV1.creditDeposit()` is the mechanism by which tokens sent directly to a `DirectDepositV1` contract are credited to a subaccount:

```solidity
// core/contracts/DirectDepositV1.sol L83-L101
function creditDeposit() external {
    uint32[] memory productIds = spotEngine.getProductIds();
    for (uint256 i = 0; i < productIds.length; i++) {
        uint32 productId = productIds[i];
        address tokenAddr = spotEngine.getToken(productId);
        require(tokenAddr != address(0), "Invalid productId.");
        IIERC20Base token = IIERC20Base(tokenAddr);
        uint256 balance = token.balanceOf(address(this));
        if (balance != 0) {
            token.approve(address(endpoint), balance);          // (1) approve
            endpoint.depositCollateralWithReferral(             // (2) call
                subaccount, productId, uint128(balance), "-1"
            );
        }
    }
}
```

Step (1) sets a full-balance approval on `endpoint`. Step (2) calls into `Endpoint.depositCollateralWithReferral()`, which contains this path:

```solidity
// core/contracts/Endpoint.sol L137-L142
if (!isValidDepositAmount(subaccount, productId, amount)) {
    // we cannot revert here, otherwise direct deposit could be blocked when there are
    // multiple assets awaiting credit but one of them is below the minimum deposit amount.
    // we can just skip the deposit and continue with the next asset.
    return;
}
```

When this branch is taken, `handleDepositTransfer` is never reached, so `endpoint` never calls `transferFrom` to pull the tokens. The tokens remain in `DirectDepositV1` with a live approval to `endpoint` but no accounting entry in the protocol.

The recovery function is owner-gated:

```solidity
// core/contracts/DirectDepositV1.sol L103-L106
function withdraw(IIERC20Base token) external onlyOwner {
    uint256 balance = token.balanceOf(address(this));
    safeTransfer(token, msg.sender, balance);
}
```

`DirectDepositV1` is deployed by `ContractOwner` (via `createDirectDepositV1`), making `ContractOwner` the Ownable owner. The user who sent tokens has no on-chain path to recover them.

Additionally, `creditDeposit()` carries no access control — any unprivileged caller can trigger it at any time, including before the balance reaches the minimum threshold, forcing the silent-skip path.

---

### Impact Explanation

A user who sends tokens to their `DirectDepositV1` address expecting them to be deposited into their subaccount will find those tokens silently stranded if the balance is below `MIN_DEPOSIT_AMOUNT` (or `MIN_FIRST_DEPOSIT_AMOUNT` for a new subaccount). The tokens are not credited to the subaccount, not returned to the user, and not recoverable by the user. Recovery requires the protocol admin (`ContractOwner`) to call `withdraw()` on behalf of the user. If admin action is delayed or unavailable, the funds are effectively frozen indefinitely.

---

### Likelihood Explanation

The trigger condition is realistic and reachable by any unprivileged actor:

1. A user sends a small token amount to their `DirectDepositV1` address (e.g., a dust amount, a test deposit, or a partial fill from an external bridge).
2. Anyone (including the user themselves, or a bot) calls `creditDeposit()`.
3. `isValidDepositAmount` returns `false` because the balance is below the minimum.
4. Tokens are stranded.

The code comment at `Endpoint.sol` L138–141 explicitly acknowledges this silent-skip behavior as intentional for multi-asset iteration, but does not address the stranding consequence. The lack of access control on `creditDeposit()` means an attacker can race to call it before the balance accumulates to the minimum, forcing the skip path.

---

### Recommendation

1. **Post-call balance check**: After `endpoint.depositCollateralWithReferral()` returns, check whether the token balance of `DirectDepositV1` decreased. If not (i.e., the deposit was skipped), reset the approval to zero and emit an event so the user is notified.
2. **Revoke residual approval**: Always call `token.approve(address(endpoint), 0)` after the deposit call, regardless of outcome.
3. **Access control on `creditDeposit()`**: Restrict callers to the owner or the subaccount owner to prevent griefing via premature invocation.
4. **User-accessible recovery**: Add a function allowing the subaccount owner (derived from `subaccount`) to withdraw stranded tokens directly, without requiring admin intervention.

---

### Proof of Concept

1. Protocol has `MIN_DEPOSIT_AMOUNT = 10e18` (example).
2. User sends `5e18` USDC to their `DirectDepositV1` address.
3. Attacker (or anyone) calls `DirectDepositV1.creditDeposit()`.
4. Inside `creditDeposit()`:
   - `balance = 5e18`
   - `token.approve(endpoint, 5e18)` — approval set
   - `endpoint.depositCollateralWithReferral(subaccount, productId, 5e18, "-1")` called
5. Inside `depositCollateralWithReferral()`:
   - `isValidDepositAmount(subaccount, productId, 5e18)` returns `false` (below minimum)
   - Function returns early; `handleDepositTransfer` is never called
6. Result: `5e18` USDC remains in `DirectDepositV1`, not credited to the subaccount, with a live `5e18` approval to `endpoint`. User has no recovery path. Only `ContractOwner` can call `withdraw()`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
