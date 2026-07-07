### Title
Replacing `withdrawPool` Without Migrating Replay-Protection State Enables Fast Withdrawal Double-Claim on New Pool — (File: `core/contracts/Clearinghouse.sol`)

---

### Summary

`Clearinghouse.setWithdrawPool` allows the owner to replace the active `withdrawPool` address with a new contract. The new pool starts with empty `markedIdxs` and `minIdx = 0`. Any fast withdrawal transaction previously processed by the old pool can be re-submitted to the new pool by an unprivileged caller, because the new pool has no record of prior processing. This drains tokens that legitimate users deposited into the new pool via normal withdrawals.

---

### Finding Description

`Clearinghouse.setWithdrawPool` overwrites the `withdrawPool` storage variable with no guard against an already-set value and no state migration: [1](#0-0) 

The `withdrawPool` contract (`BaseWithdrawPool`) maintains two replay-protection state variables:

- `markedIdxs`: a `mapping(uint64 => bool)` that records every withdrawal index already processed [2](#0-1) 
- `minIdx`: the floor index below which fast withdrawals are rejected [3](#0-2) 

`submitFastWithdrawal` enforces replay protection exclusively through these two fields: [4](#0-3) 

When the owner calls `setWithdrawPool(newPool)`, the new pool is a freshly deployed contract: `markedIdxs` is entirely empty and `minIdx = 0`. Every fast withdrawal that was legitimately processed by the old pool now passes both checks on the new pool. The Clearinghouse transfers tokens to `withdrawPool` atomically during each new withdrawal: [5](#0-4) 

So the new pool accumulates real token balances from ongoing protocol activity, which the replayed transactions drain.

The `addEngine` function, by contrast, correctly guards against overwriting an already-registered engine: [6](#0-5) 

No equivalent guard exists in `setWithdrawPool`.

---

### Impact Explanation

An unprivileged caller who previously submitted a fast withdrawal (or who observed one on-chain) can re-submit the identical `transaction` bytes and `signatures` to the new pool's `submitFastWithdrawal`. The verifier signatures remain valid because they are bound to the transaction content and index, not to a specific pool address. The new pool pays out the full withdrawal amount again to the original `sendTo` address. Every fast withdrawal ever processed by the old pool is replayable, so the new pool can be fully drained up to its token balance. The corrupted state delta is `fees[productId]` and the token balance of the new pool.

---

### Likelihood Explanation

Low-to-medium. The precondition is a legitimate owner-initiated pool migration (a plausible operational event during upgrades or emergency response). Once the new pool is live and has received even one normal withdrawal from the Clearinghouse, the replay window opens immediately. The attacker needs only the original transaction calldata and signatures, which are permanently visible in on-chain history. No privileged access is required after the migration.

---

### Recommendation

In `setWithdrawPool`, either:

1. **Revert if already set**: add `require(withdrawPool == address(0))` to prevent silent overwrites, forcing an explicit migration path, or
2. **Seed `minIdx` on migration**: initialize the new pool's `minIdx` to the old pool's current `minIdx` before switching, so all previously valid indices are below the floor, or
3. **Migrate `markedIdxs`**: copy the set of processed indices from the old pool to the new pool before activating it.

---

### Proof of Concept

1. Protocol is live; `withdrawPool = OldPool`. User A submits a fast withdrawal: `idx = 500`, `amount = 10,000 USDC`. `OldPool.markedIdxs[500] = true`, `OldPool.minIdx = 500`.
2. Owner calls `Clearinghouse.setWithdrawPool(NewPool)`. `NewPool.markedIdxs` is empty; `NewPool.minIdx = 0`.
3. Subsequent normal withdrawals flow through `Clearinghouse.handleWithdrawTransfer` → tokens are transferred to `NewPool`, which now holds, say, 50,000 USDC.
4. User A (or any observer) calls `NewPool.submitFastWithdrawal(500, originalTxBytes, originalSigs)`.
   - `!markedIdxs[500]` → `true` ✓
   - `500 > minIdx (0)` → `true` ✓
   - `Verifier.requireValidTxSignatures` → passes (same bytes, same signatures) ✓
5. `NewPool` pays out 10,000 USDC to User A again. Step 4 can be repeated for every historical fast withdrawal, draining `NewPool`.

### Citations

**File:** core/contracts/Clearinghouse.sol (L161-161)
```text
        require(address(engineByType[engineType]) == address(0));
```

**File:** core/contracts/Clearinghouse.sol (L382-385)
```text
    ) internal virtual {
        token.safeTransfer(withdrawPool, uint256(amount));
        BaseWithdrawPool(withdrawPool).submitWithdrawal(token, to, amount, idx);
    }
```

**File:** core/contracts/Clearinghouse.sol (L750-753)
```text
    function setWithdrawPool(address _withdrawPool) external onlyOwner {
        require(_withdrawPool != address(0));
        withdrawPool = _withdrawPool;
    }
```

**File:** core/contracts/BaseWithdrawPool.sol (L37-37)
```text
    mapping(uint64 => bool) public markedIdxs;
```

**File:** core/contracts/BaseWithdrawPool.sol (L42-42)
```text
    uint64 public minIdx;
```

**File:** core/contracts/BaseWithdrawPool.sol (L86-88)
```text
        require(!markedIdxs[idx], "Withdrawal already submitted");
        require(idx > minIdx, "idx too small");
        markedIdxs[idx] = true;
```
