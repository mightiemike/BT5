### Title
Missing `sendTo` Address Validation in Fast Withdrawal Allows User Funds to Be Locked in WithdrawPool — (`core/contracts/BaseWithdrawPool.sol`)

---

### Summary

`BaseWithdrawPool.submitFastWithdrawal` resolves a user-signed `WithdrawCollateralV2` transaction and transfers tokens to the caller-supplied `sendTo` address without validating that `sendTo` is not the `WithdrawPool` contract itself (or the `Clearinghouse`). If a user signs a withdrawal with `sendTo = address(withdrawPool)`, the pool pays itself (a no-op transfer), the sequencer later decrements the user's on-chain balance and replenishes the pool, and the user never receives their tokens. Funds are permanently locked in the pool from the user's perspective.

---

### Finding Description

`resolveFastWithdrawal` extracts `sendTo` from a user-signed `WithdrawCollateralV2` transaction: [1](#0-0) 

The resolved address is passed directly to `handleWithdrawTransfer` with no guard against critical contract addresses: [2](#0-1) 

`handleWithdrawTransfer` executes an unconditional `safeTransfer` to `to`: [3](#0-2) 

If `sendTo == address(this)` (the `WithdrawPool`), the transfer is a self-transfer — tokens remain in the pool. The sequencer subsequently processes the slow-mode withdrawal, which decrements the user's `SpotEngine` balance via `Clearinghouse.withdrawCollateral` and replenishes the pool: [4](#0-3) 

`submitWithdrawal` then finds `markedIdxs[idx] == true` (set during the fast withdrawal) and returns early without paying the user: [5](#0-4) 

The same root cause exists in the slow-mode-only path: `withdrawCollateral` accepts `sendTo` from the sequencer-relayed user transaction and passes it to `handleWithdrawTransfer` without checking `sendTo != address(withdrawPool)` and `sendTo != address(this)`: [6](#0-5) 

---

### Impact Explanation

A user who signs a `WithdrawCollateralV2` with `sendTo = address(withdrawPool)` (accidentally or through a UI bug) will have their `SpotEngine` balance permanently decremented while receiving zero tokens. The tokens accumulate as unattributed liquidity in the `WithdrawPool`. The only recovery path is `removeLiquidity`, which is `onlyOwner` and does not restore the user's balance — it sends funds to an owner-chosen address, not back to the victim. [7](#0-6) 

**Corrupted state delta:** `SpotEngine` balance for the user is reduced by `amount`; the user receives 0 tokens; `WithdrawPool` token balance increases by `amount` with no corresponding liability.

---

### Likelihood Explanation

`submitFastWithdrawal` is a public function callable by any address. The `sendTo` field in `WithdrawCollateralV2` is user-controlled at signing time. A user who pastes the `WithdrawPool` contract address (a well-known protocol address) as the recipient — a plausible UI or copy-paste mistake — triggers the loss with no on-chain protection. No privileged access is required. [8](#0-7) 

---

### Recommendation

**Short term:** Add an address validation guard in `resolveFastWithdrawal` (or at the top of `submitFastWithdrawal`) and in `Clearinghouse.withdrawCollateral` to reject withdrawals where `sendTo` equals the `WithdrawPool` address, the `Clearinghouse` address, or `address(0)`:

```solidity
require(
    sendTo != address(this) &&
    sendTo != clearinghouse &&
    sendTo != address(0),
    "Invalid sendTo address"
);
```

**Long term:** Adopt invariant-based fuzzing (e.g., Echidna) to assert that after any withdrawal the recipient's token balance increases by exactly the withdrawn amount and no protocol contract address appears as a net beneficiary of a user withdrawal.

---

### Proof of Concept

1. User signs a `WithdrawCollateralV2` transaction with `sendTo = address(withdrawPool)` and `amount = X`.
2. Anyone calls `BaseWithdrawPool.submitFastWithdrawal(idx, transaction, signatures)`.
3. `resolveFastWithdrawal` returns `sendTo = address(withdrawPool)`.
4. `handleWithdrawTransfer(token, address(withdrawPool), X)` executes `token.safeTransfer(address(withdrawPool), X)` — pool balance unchanged (self-transfer).
5. `markedIdxs[idx]` is set to `true`.
6. Sequencer later calls `Clearinghouse.withdrawCollateral(sender, productId, X, address(withdrawPool), idx)`.
7. `spotEngine.updateBalance(productId, sender, -X * multiplier)` — user balance decremented.
8. `handleWithdrawTransfer(token, address(withdrawPool), X, idx)` sends `X` tokens from clearinghouse to pool, then calls `submitWithdrawal`.
9. `submitWithdrawal` finds `markedIdxs[idx] == true`, returns early — no transfer to user.
10. **Result:** User's balance is zero; user received 0 tokens; `X` tokens are locked in `WithdrawPool`. [9](#0-8)

### Citations

**File:** core/contracts/BaseWithdrawPool.sol (L67-77)
```text
        if (txType == IEndpoint.TransactionType.WithdrawCollateralV2) {
            IEndpoint.SignedWithdrawCollateralV2 memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedWithdrawCollateralV2)
            );
            // V2 appendix is intentionally ignored until fast-withdraw features use it.
            address resolvedSendTo = signedTx.tx.sendTo == address(0)
                ? address(uint160(bytes20(signedTx.tx.sender)))
                : signedTx.tx.sendTo;
            return (signedTx.tx.productId, resolvedSendTo, signedTx.tx.amount);
        }
```

**File:** core/contracts/BaseWithdrawPool.sol (L81-88)
```text
    function submitFastWithdrawal(
        uint64 idx,
        bytes calldata transaction,
        bytes[] calldata signatures
    ) public {
        require(!markedIdxs[idx], "Withdrawal already submitted");
        require(idx > minIdx, "idx too small");
        markedIdxs[idx] = true;
```

**File:** core/contracts/BaseWithdrawPool.sol (L113-113)
```text
        handleWithdrawTransfer(token, sendTo, transferAmount);
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

**File:** core/contracts/Clearinghouse.sol (L403-408)
```text

        if (sendTo == address(0)) {
            sendTo = address(uint160(bytes20(sender)));
        }

        handleWithdrawTransfer(token, sendTo, amount, idx);
```
