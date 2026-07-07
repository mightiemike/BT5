Let me check the Endpoint.sol for reentrancy guards and the `withdrawCollateral` flow more carefully.

### Title
CEI Violation in `withdrawCollateral` Enables Reentrancy Before Balance Decrement — (File: `core/contracts/Clearinghouse.sol`)

### Summary

`Clearinghouse.withdrawCollateral` performs the token transfer to the recipient **before** decrementing the sender's on-chain balance in `spotEngine`. No reentrancy guard exists anywhere in the codebase. If the withdrawal token supports transfer callbacks (ERC777 `tokensReceived`, or any hook-bearing ERC20), an attacker-controlled `sendTo` contract can re-enter the endpoint during the transfer while the balance has not yet been decremented, allowing a second withdrawal to pass the health check with the original, unmodified balance.

### Finding Description

In `Clearinghouse.withdrawCollateral` the execution order is:

1. **External call first** — `handleWithdrawTransfer(token, sendTo, amount, idx)` at line 408 is called before any state mutation.
2. **State update after** — `spotEngine.updateBalance(productId, sender, amountRealized)` at line 412 runs only after the external call chain completes. [1](#0-0) 

`handleWithdrawTransfer` in `Clearinghouse` forwards the tokens to `withdrawPool` and then calls `BaseWithdrawPool.submitWithdrawal`, which in turn calls `token.safeTransfer(sendTo, amount)`: [2](#0-1) [3](#0-2) [4](#0-3) 

The `safeTransfer` to `sendTo` is the external call that hands control to the recipient. A grep across the entire repository confirms **zero uses** of `nonReentrant` or `ReentrancyGuard` anywhere in the codebase. The `Endpoint` contract, which is the only caller allowed by `onlyEndpoint`, also carries no reentrancy lock. [5](#0-4) 

Because `Endpoint` has no reentrancy guard, a callback from `sendTo` can call back into `Endpoint` (e.g., via a user-submittable slow-mode transaction that was pre-queued and whose timeout has elapsed). `msg.sender` in that re-entrant call is still `Endpoint`, so `onlyEndpoint` on `withdrawCollateral` is satisfied. At the moment of re-entry, `spotEngine` still holds the original balance — the decrement at line 412 has not executed — so the health check at line 419 passes again with the full pre-withdrawal balance. [6](#0-5) 

### Impact Explanation

An attacker who controls `sendTo` and whose withdrawal token supports transfer callbacks can drain collateral beyond their actual balance. Each re-entrant call passes the health check against the stale (undecremented) balance. The corrupted state delta is `spotEngine` balance for the victim subaccount: it is decremented only once while two (or more) token transfers have already left the `WithdrawPool`. The protocol's solvency invariant — that on-chain token balances in `Clearinghouse`/`WithdrawPool` match the sum of all `spotEngine` balances — is broken.

### Likelihood Explanation

The trigger requires a withdrawal token that delivers a callback on `transfer` (ERC777 or a custom hook-bearing ERC20). Nado supports multiple spot collateral tokens registered by the owner; any future listing of such a token immediately opens this path. The attacker needs only a valid subaccount with a pending withdrawal and a pre-queued slow-mode transaction (or any other re-entrant path into `Endpoint`). No privileged keys are required.

### Recommendation

Apply the checks-effects-interactions pattern: call `spotEngine.updateBalance` and assert health **before** calling `handleWithdrawTransfer`. Additionally, add OpenZeppelin's `ReentrancyGuardUpgradeable` to `Clearinghouse` (and `Endpoint`) and apply `nonReentrant` to `withdrawCollateral` and any other function that performs external token transfers.

### Proof of Concept

1. Attacker deploys `MaliciousRecipient` implementing ERC777 `tokensReceived`.
2. Attacker opens a subaccount, deposits 100 USDC, and submits two slow-mode `WithdrawCollateral` transactions (both for 100 USDC) to `Endpoint`. The second transaction's timeout elapses.
3. Sequencer processes the first withdrawal: `Endpoint` → `Clearinghouse.withdrawCollateral(sender, productId, 100, MaliciousRecipient, idx)`.
4. Inside `BaseWithdrawPool.handleWithdrawTransfer`, `token.safeTransfer(MaliciousRecipient, 100)` fires.
5. `MaliciousRecipient.tokensReceived` calls `Endpoint.executeSlowModeTransaction` (or equivalent user-callable slow-mode executor) for the second pre-queued withdrawal.
6. `Endpoint` calls `Clearinghouse.withdrawCollateral` again. `spotEngine` balance is still 100 (line 412 has not run). Health check passes. Second 100 USDC is transferred out.
7. Execution returns to the first call; `spotEngine.updateBalance` decrements balance by 100. Health check passes on the now-zero balance.
8. Net result: attacker received 200 USDC against a 100 USDC deposit; `spotEngine` records −100 but `WithdrawPool` has paid out 200. [1](#0-0) [4](#0-3)

### Citations

**File:** core/contracts/Clearinghouse.sol (L377-385)
```text
    function handleWithdrawTransfer(
        IERC20Base token,
        address to,
        uint128 amount,
        uint64 idx
    ) internal virtual {
        token.safeTransfer(withdrawPool, uint256(amount));
        BaseWithdrawPool(withdrawPool).submitWithdrawal(token, to, amount, idx);
    }
```

**File:** core/contracts/Clearinghouse.sol (L391-421)
```text
    function withdrawCollateral(
        bytes32 sender,
        uint32 productId,
        uint128 amount,
        address sendTo,
        uint64 idx
    ) public virtual onlyEndpoint {
        require(!RiskHelper.isIsolatedSubaccount(sender), ERR_UNAUTHORIZED);
        require(amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        ISpotEngine spotEngine = _spotEngine();
        IERC20Base token = IERC20Base(spotEngine.getConfig(productId).token);
        require(address(token) != address(0));

        if (sendTo == address(0)) {
            sendTo = address(uint160(bytes20(sender)));
        }

        handleWithdrawTransfer(token, sendTo, amount, idx);

        int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
        int128 amountRealized = -int128(amount) * int128(multiplier);
        spotEngine.updateBalance(productId, sender, amountRealized);
        spotEngine.assertUtilization(productId);

        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
        emit ModifyCollateral(amountRealized, sender, productId);
    }
```

**File:** core/contracts/BaseWithdrawPool.sol (L116-132)
```text
    function submitWithdrawal(
        IERC20Base token,
        address sendTo,
        uint128 amount,
        uint64 idx
    ) public {
        require(msg.sender == clearinghouse);

        if (markedIdxs[idx]) {
            return;
        }
        markedIdxs[idx] = true;
        // set minIdx to most recent withdrawal submitted by sequencer
        minIdx = idx;

        handleWithdrawTransfer(token, sendTo, amount);
    }
```

**File:** core/contracts/BaseWithdrawPool.sol (L184-190)
```text
    function handleWithdrawTransfer(
        IERC20Base token,
        address to,
        uint128 amount
    ) internal virtual {
        token.safeTransfer(to, uint256(amount));
    }
```

**File:** core/contracts/Endpoint.sol (L23-28)
```text
contract Endpoint is
    EIP712Upgradeable,
    OwnableUpgradeable,
    EndpointStorage,
    IEndpoint
{
```
