Let me read the key files in detail to trace the exact checkpoint advancement order relative to the pool check.

The vulnerability is real. Here is the precise trace:

**Checkpoint advancement order in `processEventsAndClaimBonus`:**

1. Line 193: `pos.UpdateLastEventSeq(entry.Seq)` — advances `LastEventSeq` on the `pos` pointer inside the event loop
2. Line 196: `k.decrementEventRefCount(...)` — **persistent** decrement of event reference counts (may garbage-collect events)
3. Line 215: `applyBonusAccrualCheckpoint(&pos.Position, blockTime)` — advances `LastBonusAccrual`
4. Line 217: `pos.UpdateLastKnownBonded(bonded)` — updates `LastKnownBonded`
5. Line 230: `k.sufficientBonusPoolBalance(...)` — pool check happens **after** all checkpoints are advanced
6. Returns `nil, ErrInsufficientBonusPool` — checkpoints already advanced on the `pos` pointer

**In `slashRedelegationPosition` for a partial slash:**

- Line 54: `processEventsAndClaimBonus(ctx, &pos)` — pointer passed, checkpoints mutated in-place
- Lines 56–63: `ErrInsufficientBonusPool` is swallowed
- Line 77: `k.setPositionWithState(ctx, pos, nil)` — persists the position with advanced checkpoints, no bonus paid

For a **full slash** (line 70), `pos.ClearBonusCheckpoints()` is called afterward, which resets the checkpoints — so the bug only manifests on **partial slashes**.

---

### Title
Checkpoint Advancement Before Pool Check in `slashRedelegationPosition` Causes Permanent Bonus Loss on Partial Redelegation Slash — (`x/tieredrewards/keeper/slash.go`, `x/tieredrewards/keeper/claim_rewards.go`)

### Summary
When `BeforeRedelegationSlashed` fires for a tier position during a partial redelegation slash and the `RewardsPoolName` module account has insufficient balance, `processEventsAndClaimBonus` advances `LastBonusAccrual`, `LastEventSeq`, and `LastKnownBonded` on the position **before** the pool sufficiency check. The `ErrInsufficientBonusPool` error is then deliberately swallowed in `slashRedelegationPosition` to avoid a chain halt, and the position is persisted with the advanced checkpoints. The accrued bonus for the pre-slash segment is permanently unrecoverable.

### Finding Description

`processEventsAndClaimBonus` mutates the `pos` pointer's checkpoints unconditionally before checking pool balance: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

The pool check at line 230 returns `ErrInsufficientBonusPool` **after** `LastEventSeq`, `LastBonusAccrual`, and `LastKnownBonded` are already written to the `pos` pointer, and after event reference counts are decremented in persistent store.

In `slashRedelegationPosition`, the error is explicitly swallowed for the insufficient-pool case: [5](#0-4) 

For a partial slash, the position is then persisted with the advanced checkpoints: [6](#0-5) 

For a full slash, `ClearBonusCheckpoints()` is called afterward, which resets the checkpoints — so the bug only manifests on partial slashes: [7](#0-6) 

The `ClearBonusCheckpoints` function zeros all three fields: [8](#0-7) 

### Impact Explanation

The position owner permanently loses all bonus rewards accrued since `LastBonusAccrual` up to the slash block time. The checkpoints have advanced past the accrual window, so no future `MsgClaimTierRewards` call can recover those rewards. The event reference counts are also decremented, potentially garbage-collecting the events, making recovery impossible even if checkpoints were somehow reset. This is a direct, unrecoverable loss of tokens owed to the position owner from the `RewardsPoolName` module account — the tokens remain in the pool but are permanently inaccessible to the rightful recipient.

### Likelihood Explanation

The conditions required are:
1. A tier position has an active redelegation tracked in `RedelegationMappings` (created by `MsgTierRedelegate`)
2. The source validator is slashed during the redelegation period (e.g., double-signing), triggering `BeforeRedelegationSlashed`
3. The `RewardsPoolName` module account balance is below the accrued bonus amount at the time of the slash

Condition 3 is realistic: the pool is consumed by normal bonus claims from all positions and by the BeginBlocker base-rewards top-up. If the pool is not actively replenished by governance, it can be drained. The slash event itself is externally triggered (validator misbehavior), not attacker-controlled, but the pool depletion can be engineered by an attacker who drains the pool via legitimate `MsgClaimTierRewards` calls before the slash fires.

### Recommendation

Move the pool sufficiency check and the payment **before** advancing the checkpoints, or — preferably — do not advance checkpoints on the `pos` pointer when returning an error. The simplest fix is to defer checkpoint mutation until after the payment succeeds:

```go
// Only advance checkpoints after successful payment
if err := k.bankKeeper.SendCoinsFromModuleToAccount(...); err != nil {
    return nil, err
}
applyBonusAccrualCheckpoint(&pos.Position, blockTime)
pos.UpdateLastKnownBonded(bonded)
// also defer UpdateLastEventSeq calls inside the loop
```

Alternatively, in `slashRedelegationPosition`, when `ErrInsufficientBonusPool` is caught, explicitly reset the position's checkpoints to their pre-call values before persisting, so the owner can retry the claim once the pool is replenished.

### Proof of Concept

```go
func TestSlashRedelegationPosition_InsufficientPool_CheckpointsAdvanceWithoutPayment(t *testing.T) {
    // 1. Setup: create a tier position with an active redelegation
    //    (use setupRedelegatingPosition from slash_test.go)
    // 2. Advance block time by 30 days so bonus accrues
    // 3. Drain RewardsPoolName to zero via MsgClaimTierRewards on other positions
    //    or directly via bankKeeper.SendCoinsFromModuleToAccount
    // 4. Record owner balance before and position.LastBonusAccrual before
    preAccrual := pos.LastBonusAccrual
    balBefore := bankKeeper.GetBalance(ctx, owner, bondDenom)
    // 5. Trigger BeforeRedelegationSlashed with a partial sharesToUnbond
    err := keeper.Hooks().BeforeRedelegationSlashed(ctx, unbondingID, partialShares)
    require.NoError(t, err) // error is swallowed — no chain halt
    // 6. Assert: owner balance unchanged (no bonus paid)
    balAfter := bankKeeper.GetBalance(ctx, owner, bondDenom)
    require.Equal(t, balBefore, balAfter, "no bonus should have been paid")
    // 7. Assert: LastBonusAccrual advanced past preAccrual (BUG: checkpoint moved without payment)
    updated, _ := keeper.GetPositionState(ctx, pos.Id)
    require.True(t, updated.LastBonusAccrual.After(preAccrual),
        "BUG: checkpoint advanced without paying bonus")
    // 8. Assert: subsequent MsgClaimTierRewards yields zero bonus for the lost segment
    //    (even after pool is replenished, the window is gone)
    fundRewardsPool(largeAmount)
    bonus, _ := keeper.ProcessEventsAndClaimBonus(ctx, &updated)
    require.True(t, bonus.IsZero(), "BUG: accrued bonus permanently lost")
}
```

### Citations

**File:** x/tieredrewards/keeper/claim_rewards.go (L193-193)
```go
		pos.UpdateLastEventSeq(entry.Seq)
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L196-198)
```go
		if err := k.decrementEventRefCount(ctx, valAddr, entry.Seq); err != nil {
			return nil, err
		}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L215-217)
```go
	applyBonusAccrualCheckpoint(&pos.Position, blockTime)
	// Persist the bonded state so the next replay starts correctly.
	pos.UpdateLastKnownBonded(bonded)
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L230-232)
```go
	if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/slash.go (L54-64)
```go
	if _, err := k.processEventsAndClaimBonus(ctx, &pos); err != nil {
		// Deliberately forgo bonus rewards if pool is insufficient to prevent chain halt.
		if errors.Is(err, types.ErrInsufficientBonusPool) {
			k.logger(ctx).Error("insufficient bonus pool during redelegation slash",
				"position_id", pos.Id,
				"error", err.Error(),
			)
		} else {
			return err
		}
	}
```

**File:** x/tieredrewards/keeper/slash.go (L68-71)
```go
	if fullSlash {
		pos.Delegation = nil
		pos.ClearBonusCheckpoints()
		return k.setPositionWithState(ctx, pos, &ValidatorTransition{PreviousAddress: dstValStr})
```

**File:** x/tieredrewards/keeper/slash.go (L76-77)
```go
	pos.Delegation.Shares = pos.Delegation.Shares.Sub(sharesToUnbond)
	return k.setPositionWithState(ctx, pos, nil)
```

**File:** x/tieredrewards/types/position.go (L80-84)
```go
func (p *Position) ClearBonusCheckpoints() {
	p.LastBonusAccrual = time.Time{}
	p.LastEventSeq = 0
	p.LastKnownBonded = false
}
```
