### Title
Fast Withdrawal Fees Accumulate in `WithdrawPool` Without SpotEngine Credit, Creating Permanent Accounting Desynchronization — (`core/contracts/BaseWithdrawPool.sol`)

---

### Summary

In `BaseWithdrawPool.submitFastWithdrawal()`, fees collected from fast-withdrawal callers are tracked in a local `fees[productId]` mapping but are **never credited to any SpotEngine subaccount**. This creates a permanent desynchronization between the `WithdrawPool`'s actual ERC-20 token balance and the SpotEngine's internal accounting, causing fee revenue to be permanently stranded in the `WithdrawPool` with no on-chain distribution path.

---

### Finding Description

`submitFastWithdrawal` is a publicly callable function that allows any caller to service a signed user withdrawal ahead of the sequencer. It charges a fee in two ways: [1](#0-0) 

```solidity
int128 fee = fastWithdrawalFeeAmount(token, productId, transferAmount);

if (sendTo == msg.sender) {
    require(transferAmount > uint128(fee), "Fee larger than balance");
    transferAmount -= uint128(fee);          // user receives less
} else {
    safeTransferFrom(token, msg.sender, uint128(fee));  // caller pays fee in
}

fees[productId] += fee;                      // tracked locally only

handleWithdrawTransfer(token, sendTo, transferAmount);
```

In **both branches**, the fee is recorded in `fees[productId]` and the tokens remain inside the `WithdrawPool`. However, there is **no corresponding `spotEngine.updateBalance()` call** to credit these fee tokens to any protocol subaccount (e.g., `X_ACCOUNT`, `FEES_ACCOUNT`, or a treasury subaccount).

Contrast this with how the sequencer fee path works in `Clearinghouse.claimSequencerFees()`, which explicitly calls `spotEngine.updateBalance(spotIds[i], X_ACCOUNT, fees[i] + feeBalance.amount)` to reconcile fees into the engine's accounting: [2](#0-1) 

No equivalent reconciliation exists for `WithdrawPool.fees`. The `fees` mapping is **write-only** — it is incremented in `submitFastWithdrawal` but never read by any function that credits SpotEngine. The only escape valve is `removeLiquidity`, which is owner-gated and also does not update SpotEngine: [3](#0-2) 

---

### Impact Explanation

Every fast withdrawal permanently widens the gap between:

- **Actual token balance** of `WithdrawPool` (includes accumulated `fees[productId]`)
- **SpotEngine-tracked balance** (does not include those fee tokens — they belong to no subaccount)

The fee tokens are stranded inside `WithdrawPool` with no on-chain path to distribute them to the protocol treasury or any fee recipient. The protocol's fast-withdrawal fee revenue is effectively unclaimable through normal protocol operations. The only recovery is an owner-privileged `removeLiquidity` call, which itself does not update SpotEngine accounting, perpetuating the desynchronization.

Additionally, because `WithdrawPool` holds more tokens than SpotEngine accounts for, any off-chain or on-chain solvency check that compares `WithdrawPool.checkProductBalances()` against SpotEngine deposit totals will produce a false surplus, masking the true state of the protocol's liquidity.

---

### Likelihood Explanation

The trigger is **every call to `submitFastWithdrawal`** — a permissionless, publicly callable function. No special role, governance action, or unusual condition is required. The desynchronization grows monotonically with protocol usage.

---

### Recommendation

After collecting the fee, credit it to the appropriate protocol subaccount in SpotEngine. Mirroring the pattern used in `Clearinghouse.claimSequencerFees`:

```solidity
fees[productId] += fee;

// Credit fee to protocol fee account in SpotEngine
spotEngine().updateBalance(
    productId,
    FEES_ACCOUNT,   // or X_ACCOUNT / treasury subaccount
    int128(fee)
);
```

This ensures the SpotEngine's accounting stays synchronized with the `WithdrawPool`'s actual token balance, consistent with how all other fee flows in the protocol are handled.

---

### Proof of Concept

1. `WithdrawPool` holds 1000 USDC. SpotEngine tracks 1000 USDC across all subaccounts.
2. User signs a withdrawal for 100 USDC. A third-party caller calls `submitFastWithdrawal` with `sendTo == msg.sender` (caller is the recipient).
3. Fee = 0.5 USDC. `transferAmount` becomes 99.5 USDC. `fees[productId] += 0.5`.
4. `WithdrawPool` sends 99.5 USDC to caller. `WithdrawPool` balance = 900.5 USDC.
5. Sequencer later processes the withdrawal: SpotEngine debits user's subaccount by 100 USDC. SpotEngine total = 900 USDC.
6. **Desync**: `WithdrawPool` holds 900.5 USDC; SpotEngine accounts for 900 USDC. The 0.5 USDC fee belongs to no subaccount.
7. After N fast withdrawals, `fees[productId]` = N × 0.5 USDC stranded in `WithdrawPool`, permanently unaccounted for in SpotEngine, and inaccessible without an owner `removeLiquidity` call. [4](#0-3)

### Citations

**File:** core/contracts/BaseWithdrawPool.sol (L81-114)
```text
    function submitFastWithdrawal(
        uint64 idx,
        bytes calldata transaction,
        bytes[] calldata signatures
    ) public {
        require(!markedIdxs[idx], "Withdrawal already submitted");
        require(idx > minIdx, "idx too small");
        markedIdxs[idx] = true;

        Verifier v = Verifier(verifier);
        v.requireValidTxSignatures(transaction, idx, signatures);

        (
            uint32 productId,
            address sendTo,
            uint128 transferAmount
        ) = resolveFastWithdrawal(transaction);
        IERC20Base token = getToken(productId);

        require(transferAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);

        int128 fee = fastWithdrawalFeeAmount(token, productId, transferAmount);

        if (sendTo == msg.sender) {
            require(transferAmount > uint128(fee), "Fee larger than balance");
            transferAmount -= uint128(fee);
        } else {
            safeTransferFrom(token, msg.sender, uint128(fee));
        }

        fees[productId] += fee;

        handleWithdrawTransfer(token, sendTo, transferAmount);
    }
```

**File:** core/contracts/BaseWithdrawPool.sol (L151-157)
```text
    function removeLiquidity(
        uint32 productId,
        uint128 amount,
        address sendTo
    ) external onlyOwner {
        handleWithdrawTransfer(getToken(productId), sendTo, amount);
    }
```

**File:** core/contracts/Clearinghouse.sol (L580-594)
```text
        for (uint256 i = 0; i < spotIds.length; i++) {
            ISpotEngine.Balance memory feeBalance = spotEngine.getBalance(
                spotIds[i],
                FEES_ACCOUNT
            );
            spotEngine.updateBalance(
                spotIds[i],
                X_ACCOUNT,
                fees[i] + feeBalance.amount
            );
            spotEngine.updateBalance(
                spotIds[i],
                FEES_ACCOUNT,
                -feeBalance.amount
            );
```
