I have all the code I need. Let me trace the exact execution path.

### Title
Checkpoint Advancement Before Pool Check in `processEventsAndClaimBonus` Causes Permanent Bonus Loss When Pool Is Drained During Redelegation Slash — (`x/tieredrewards/keeper/claim_rewards.go`, `x/tieredrewards/keeper/slash.go`)

---

### Summary

`processEventsAndClaimBonus` mutates the position's bonus checkpoints (`LastBonusAccrual`, `LastEventSeq`, `LastKnownBonded`) via a pointer **before** calling `sufficientBonusPoolBalance`. When the pool check fails and `ErrInsufficientBonusPool` is returned, the caller `slashRedelegationPosition` deliberately swallows the error (chain-halt avoidance) and then calls `setPositionWithState` with the already-mutated position. The result is that the position's accrual window is permanently consumed with no bonus paid. An attacker who drains the pool via `ClaimTierRewards` before a redelegation slash fires can cause a victim's legitimately accrued bonus to be permanently forfeited.

---

### Finding Description

**Step 1 — Checkpoint mutation before pool check in `processEventsAndClaimBonus`:**

`processEventsAndClaimBonus` takes `pos *types.PositionState` (a pointer). Inside the function, before the pool balance check, it unconditionally advances all three bonus checkpoints:

```
line 193: pos.UpdateLastEventSeq(entry.Seq)   // inside event loop
line 215: applyBonusAccrualCheckpoint(&pos.Position, blockTime)
line 217: pos.UpdateLastKnownBonded(bonded)
```

Only after these mutations does it call `sufficientBonusPoolBalance` at line 230. If the pool is insufficient, the function returns `nil, ErrInsufficientBonusPool` — but `pos` has already been mutated in the caller's memory. [1](#0-0) 

**Step 2 — Error swallowed in `slashRedelegationPosition`, mutated `pos` saved:**

`slashRedelegationPosition` catches `ErrInsufficientBonusPool` and logs it, then continues execution with the already-mutated `pos`:

```go
if _, err := k.processEventsAndClaimBonus(ctx, &pos); err != nil {
    if errors.Is(err, types.ErrInsufficientBonusPool) {
        k.logger(ctx).Error(...)   // swallowed
    } else {
        return err
    }
}
// pos.LastBonusAccrual, LastEventSeq, LastKnownBonded are all advanced
// but no bonus was paid
...
return k.setPositionWithState(ctx, pos, nil)   // saves mutated pos
``` [2](#0-1) 

For a **partial slash**, the position is saved with advanced checkpoints and no bonus paid. The accrual window is permanently consumed. For a **full slash**, `ClearBonusCheckpoints()` is additionally called, but the position is also undelegated so no future claim is possible regardless. [3](#0-2) 

**Step 3 — Attacker drain path:**

`ClaimTierRewards` → `claimRewardsAndUpdatesPositions` → `processEventsAndClaimBonus` → `SendCoinsFromModuleToAccount(RewardsPoolName, owner, bonusCoins)`. An attacker with a large position and large accrued bonus can drain the pool to near-zero in a single transaction. This is a fully permissionless, owner-signed path. [4](#0-3) 

**Step 4 — Timing:**

In Cosmos SDK, `BeginBlock` (where slashes fire via evidence or the slashing module's downtime logic) runs before DeliverTx. The attacker drains the pool in block N; the slash fires in block N+1 or later. The attacker does not need to be in the same block as the slash.

---

### Impact Explanation

The victim's position owner permanently loses all accrued bonus rewards for the period between `LastBonusAccrual` and the slash block time. The bonus is not transferred to the attacker — it is simply destroyed (never paid). The position's checkpoints are advanced past the accrual window, so no retry is possible even after the pool is replenished. This is a direct, irreversible fund loss for the victim.

The partial-slash case is the most impactful: the position remains delegated and active, but the entire pre-slash bonus accrual is silently forfeited with no on-chain indication to the victim beyond a keeper log line. [5](#0-4) 

---

### Likelihood Explanation

- The attacker needs a position with enough accrued bonus to drain the pool. This is achievable by any long-standing position holder.
- The victim needs an active redelegation entry (i.e., they called `TierRedelegate` and the redelegation has not yet matured). This is a normal user action.
- The slash must be a redelegation slash (`BeforeRedelegationSlashed`), not a standard delegation slash. Redelegation slashes occur when a validator double-signs while a redelegation from that validator is still in the unbonding period.
- The attacker cannot directly trigger the slash, but they can drain the pool and wait. The pool being empty is a precondition that the attacker can manufacture; the slash is an external event.
- The ADR explicitly documents the silent-forfeit behavior as intentional for chain-halt avoidance, but does **not** document the checkpoint-advancement-before-pool-check as intentional. The asymmetry (user paths fail atomically and preserve checkpoints via tx rollback; slash hook swallows the error and saves the mutated position) is the root bug. [6](#0-5) [7](#0-6) 

---

### Recommendation

Move `applyBonusAccrualCheckpoint` and `pos.UpdateLastKnownBonded` to **after** the successful `SendCoinsFromModuleToAccount` call, or alternatively, in `slashRedelegationPosition`, restore the original checkpoint values from a snapshot taken before calling `processEventsAndClaimBonus` when `ErrInsufficientBonusPool` is caught. This ensures that if the pool is insufficient, the position's checkpoints are not advanced, and the victim can reclaim their bonus after the pool is replenished. [8](#0-7) 

---

### Proof of Concept

```
1. Setup: fund rewards pool with exactly X tokens.
2. Attacker: create position, accrue bonus = X - ε (just below pool balance).
3. Attacker: call ClaimTierRewards → pool balance drops to ε.
4. Victim: create position, redelegate → RedelegationMappings entry created.
5. Victim: accrue bonus > ε (pool cannot cover it).
6. Trigger BeforeRedelegationSlashed for victim's unbondingId with sharesToUnbond < victim.Delegation.Shares (partial slash).
7. slashRedelegationPosition calls processEventsAndClaimBonus:
   - Checkpoints advanced in memory (LastBonusAccrual = blockTime, LastEventSeq advanced).
   - sufficientBonusPoolBalance returns ErrInsufficientBonusPool.
   - Error swallowed.
8. setPositionWithState saves victim's position with advanced checkpoints, no bonus paid.
9. Assert: victim's LastBonusAccrual == blockTime (window consumed).
10. Assert: victim's bank balance unchanged (no bonus received).
11. Replenish pool. Call ClaimTierRewards for victim.
12. Assert: bonus returned = 0 (accrual window already consumed, permanently lost).
```

### Citations

**File:** x/tieredrewards/keeper/claim_rewards.go (L213-232)
```go
	}

	applyBonusAccrualCheckpoint(&pos.Position, blockTime)
	// Persist the bonded state so the next replay starts correctly.
	pos.UpdateLastKnownBonded(bonded)

	if totalBonus.IsZero() {
		return sdk.NewCoins(), nil
	}

	bondDenom, err := k.stakingKeeper.BondDenom(ctx)
	if err != nil {
		return nil, err
	}

	bonusCoins := sdk.NewCoins(sdk.NewCoin(bondDenom, totalBonus))

	if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/slash.go (L54-77)
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

	fullSlash := sharesToUnbond.GTE(pos.Delegation.Shares)

	if fullSlash {
		pos.Delegation = nil
		pos.ClearBonusCheckpoints()
		return k.setPositionWithState(ctx, pos, &ValidatorTransition{PreviousAddress: dstValStr})
	}
	// In-memory only: the persisted Position carries no share count, and the
	// live delegation will reflect the post-Unbond shares on the next read.
	// Update the local copy so any follow-up logic in this call sees consistent state.
	pos.Delegation.Shares = pos.Delegation.Shares.Sub(sharesToUnbond)
	return k.setPositionWithState(ctx, pos, nil)
```

**File:** x/tieredrewards/keeper/msg_server.go (L429-468)
```go
func (ms msgServer) ClaimTierRewards(ctx context.Context, msg *types.MsgClaimTierRewards) (*types.MsgClaimTierRewardsResponse, error) {
	if err := msg.Validate(); err != nil {
		return nil, err
	}

	positions := make([]types.PositionState, 0, len(msg.PositionIds))
	for _, posId := range msg.PositionIds {
		pos, err := ms.getPositionState(ctx, posId)
		if err != nil {
			return nil, err
		}

		if err := ms.validateClaimRewards(pos.Position, msg.Owner); err != nil {
			return nil, err
		}

		positions = append(positions, pos)
	}

	totalBase, totalBonus, err := ms.claimRewardsAndUpdatesPositions(ctx, positions)
	if err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	if err := sdkCtx.EventManager().EmitTypedEvent(&types.EventTierRewardsClaimed{
		Owner:        msg.Owner,
		PositionIds:  msg.PositionIds,
		BaseRewards:  totalBase,
		BonusRewards: totalBonus,
	}); err != nil {
		return nil, err
	}

	return &types.MsgClaimTierRewardsResponse{
		BaseRewards:  totalBase,
		BonusRewards: totalBonus,
		PositionIds:  msg.PositionIds,
	}, nil
}
```

**File:** x/tieredrewards/types/position.go (L80-84)
```go
func (p *Position) ClearBonusCheckpoints() {
	p.LastBonusAccrual = time.Time{}
	p.LastEventSeq = 0
	p.LastKnownBonded = false
}
```

**File:** doc/architecture/adr-006.md (L293-295)
```markdown
**Insufficient pool handling:**
- **User-driven paths** (ClaimTierRewards, AddToPosition, Undelegate, Redelegate, ClearPosition): fail atomically. User retries after pool is replenished.

```

**File:** doc/architecture/adr-006.md (L349-349)
```markdown
- Pool balance is never exceeded. User paths fail atomically; the `BeforeRedelegationSlashed` hook forfeits bonus silently if the pool is short, to avoid chain halt.
```
