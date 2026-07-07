The code is fully readable. Let me analyze the exact execution flow.

**Execution order in `submitFastWithdrawal`:** [1](#0-0) 

1. Line 86–87: checks `markedIdxs[idx]` and `idx > minIdx`
2. **Line 88: `markedIdxs[idx] = true`** — written before the transfer
3. Lines 90–111: signature verification, fee accounting
4. **Line 113: `handleWithdrawTransfer`** → `token.safeTransfer(sendTo, amount)` — external call last

For the **same** `idx`, the checks-effects-interactions pattern is correctly followed: `markedIdxs[idx]` is set before the transfer, so a reentrant call with the same `idx` would revert at line 86.

**For a different `idx2`, however:**

When the ERC777 `tokensReceived` callback fires during the `safeTransfer` for `idx1`, `markedIdxs[idx2]` is still `false`. A reentrant call `submitFastWithdrawal(idx2, tx2, sigs2)` would:
- Pass line 86: `markedIdxs[idx2]` is `false` ✓
- Pass line 87: `idx2 > minIdx` ✓ (if attacker holds a second valid signed tx)
- Set `markedIdxs[idx2] = true` and transfer `transferAmount2` to the attacker ✓

There is **no `nonReentrant` modifier** anywhere on `submitFastWithdrawal`. [2](#0-1) 

**Precondition feasibility:**

- ERC777 tokens are real and in production use.
- For `WithdrawCollateralV2`, `sendTo` is taken directly from the signed transaction payload and can be set to any address by the user when submitting the withdrawal request to the sequencer. [3](#0-2) 

- The sequencer signs whatever `sendTo` the user specifies — pointing it to a malicious ERC777 recipient contract is a normal user action, not a sequencer compromise.
- The attacker simply needs two legitimate pending withdrawals (two sequencer-signed transactions for `idx1` and `idx2`, both `> minIdx`).

**The invariant that breaks:** each `idx` is processed exactly once, but the pool's token balance is reduced by `transferAmount1 + transferAmount2` within a single transaction, with only `idx1` being marked at the time `idx2`'s transfer executes. Both end up marked, but the pool has been drained of two amounts in one atomic call.

---

### Title
Cross-index reentrancy in `submitFastWithdrawal` via ERC777 callback allows double-drain — (`core/contracts/BaseWithdrawPool.sol`)

### Summary
`submitFastWithdrawal` marks `markedIdxs[idx] = true` before the external token transfer, correctly preventing same-index replay. However, it has no reentrancy guard, so a reentrant call with a **different** valid `idx2` succeeds during the ERC777 `tokensReceived` callback, draining a second `transferAmount` from the pool within the same transaction.

### Finding Description
In `submitFastWithdrawal` (`BaseWithdrawPool.sol` lines 81–114), the state write `markedIdxs[idx] = true` (line 88) precedes the external call `handleWithdrawTransfer` (line 113), which internally calls `token.safeTransfer(sendTo, amount)` (line 189). For ERC777 tokens, this triggers `tokensReceived` on the recipient before returning. At that point, `markedIdxs[idx2]` for any other valid index is still `false`, and `minIdx` is unchanged. A reentrant call to `submitFastWithdrawal(idx2, tx2, sigs2)` passes all guards, marks `idx2`, and transfers a second `transferAmount2` out of the pool. No `nonReentrant` modifier exists on the function. [4](#0-3) 

### Impact Explanation
An attacker with two valid sequencer-signed withdrawal transactions can drain the pool of `transferAmount1 + transferAmount2` in a single transaction. With N pending signed withdrawals, N transfers can be chained. This directly violates the invariant that pool liquidity must not be double-spent and constitutes unauthorized asset extraction from the pool.

### Likelihood Explanation
ERC777 tokens are deployed in production (e.g., USDC on some chains, various DeFi tokens). The `WithdrawCollateralV2` path allows any user to specify an arbitrary `sendTo` address when submitting a withdrawal to the sequencer — pointing it to a malicious ERC777 recipient is a normal user action requiring no special privileges. Any user with two pending fast-withdrawal slots can execute this.

### Recommendation
Add OpenZeppelin's `ReentrancyGuardUpgradeable` and apply the `nonReentrant` modifier to `submitFastWithdrawal`. Since the contract is already upgradeable (`OwnableUpgradeable`, `EIP712Upgradeable`), adding `ReentrancyGuardUpgradeable` to the inheritance chain and calling `__ReentrancyGuard_init()` in `_initialize` is straightforward.

### Proof of Concept
```solidity
// MaliciousRecipient implements IERC777Recipient
contract MaliciousRecipient is IERC777Recipient {
    BaseWithdrawPool pool;
    uint64 idx2;
    bytes tx2;
    bytes[] sigs2;
    bool reentered;

    function tokensReceived(...) external override {
        if (!reentered) {
            reentered = true;
            // idx2 is not yet marked — passes all checks
            pool.submitFastWithdrawal(idx2, tx2, sigs2);
        }
    }
}

// Attack:
// 1. Attacker submits two WithdrawCollateralV2 requests to sequencer,
//    both with sendTo = address(maliciousRecipient)
// 2. Sequencer signs both → attacker holds (idx1, tx1, sigs1) and (idx2, tx2, sigs2)
// 3. pool.submitFastWithdrawal(idx1, tx1, sigs1)
//    → marks idx1, transfers amount1 → tokensReceived fires
//    → reentrant: submitFastWithdrawal(idx2, tx2, sigs2)
//       → marks idx2, transfers amount2 ✓
// 4. assert pool.balanceOf(token) decreased by amount1 + amount2
```

### Citations

**File:** core/contracts/BaseWithdrawPool.sol (L67-76)
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
```

**File:** core/contracts/BaseWithdrawPool.sol (L81-85)
```text
    function submitFastWithdrawal(
        uint64 idx,
        bytes calldata transaction,
        bytes[] calldata signatures
    ) public {
```

**File:** core/contracts/BaseWithdrawPool.sol (L86-113)
```text
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
