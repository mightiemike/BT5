### Title
Permanent Loss of Accrued Bonus Rewards When `BeforeRedelegationSlashed` Fires With Empty Pool — (`x/tieredrewards/keeper/slash.go`)

---

### Summary

`slashRedelegationPosition` calls `processEventsAndClaimBonus` to settle pre-slash bonus, then silently swallows `ErrInsufficientBonusPool`. The problem is that `processEventsAndClaimBonus` mutates the in-memory `pos` checkpoints (`LastBonusAccrual`, `LastEventSeq`, `LastKnownBonded`) and writes event reference-count decrements to the store **before** it reaches the pool-balance check. When the error is swallowed, `setPositionWithState` then persists those advanced checkpoints. The pre-slash bonus segment is neither paid nor recoverable on any future claim.

---

### Finding Description

**Step 1 — Checkpoint mutation before pool check in `processEventsAndClaimBonus`**

Inside the event loop (lines 172–198), for every pending event the function:
- Advances `pos.LastEventSeq` in-memory via `pos.UpdateLastEventSeq(entry.Seq)` [1](#0-0) 
- Writes event reference-count decrements to the store via `k.decrementEventRefCount` (potentially garbage-collecting the event) [2](#0-1) 

After the loop, before the pool check:
- `applyBonusAccrualCheckpoint` advances `pos.LastBonusAccrual` to `blockTime` [3](#0-2) 
- `pos.UpdateLastKnownBonded` updates the bonded state [4](#0-3) 

Only **after** all of the above does the pool check fire and return `ErrInsufficientBonusPool`: [5](#0-4) 

**Step 2 — Error swallowed in `slashRedelegationPosition`**

`slashRedelegationPosition` catches `ErrInsufficientBonusPool` and logs it, then continues execution with the already-mutated `pos`: [6](#0-5) 

**Step 3 — Advanced checkpoints persisted**

For a partial slash, `setPositionWithState(ctx, pos, nil)` is called with the mutated `pos`, writing the advanced `LastBonusAccrual`, `LastEventSeq`, and `LastKnownBonded` to the store: [7](#0-6) 

For a full slash, `pos.ClearBonusCheckpoints()` is called first (line 70), which zeros the checkpoints — but the position is also undelegated, so no future claim is possible regardless. The bonus is still permanently lost. [8](#0-7) 

**Step 4 — No recovery path**

On the next `ClaimTierRewards`, `processEventsAndClaimBonus` starts from the persisted `LastBonusAccrual` and `LastEventSeq`. Since those were advanced to the slash block time and all events were reference-count-decremented (and potentially GC-ed), the pre-slash segment `[original_LastBonusAccrual, slash_blockTime]` is permanently gone. The ADR itself acknowledges this: "Bonus forfeits silently if the pool is insufficient (chain-halt avoidance)." [9](#0-8) 

The `sufficientBonusPoolBalance` function confirms the pool check is a hard gate — it returns an error if pool balance is below the owed bonus: [10](#0-9) 

---

### Impact Explanation

A position owner who has accrued bonus rewards over a bonded period loses those rewards permanently if the `RewardsPool` is empty at the moment `BeforeRedelegationSlashed` fires. The loss is not a rounding artifact — it is the full computed segment bonus for `[LastBonusAccrual, slash_blockTime]`. After the pool is refunded, a subsequent `ClaimTierRewards` call pays zero for that segment because the checkpoints have already been advanced past it. This is a direct, permanent loss of bonus rewards owed to the position owner.

---

### Likelihood Explanation

The `RewardsPool` can be empty under normal operating conditions:
- The BeginBlocker top-up mechanism drains the pool to cover base reward shortfalls. [11](#0-10) 
- Many concurrent `ClaimTierRewards` calls can drain the pool between top-ups.
- The pool starts empty and requires explicit governance funding.

A redelegation slash fires whenever a validator that is the destination of an active redelegation is slashed for double-signing. This is a normal production event. The combination of an empty pool and a redelegation slash is realistic and requires no attacker control — it can happen organically.

---

### Recommendation

Move the pool-balance check and the coin transfer **before** any mutation of `pos` checkpoints in `processEventsAndClaimBonus`. Specifically:

1. Compute `totalBonus` and `bonusCoins` first.
2. Call `sufficientBonusPoolBalance` and `SendCoinsFromModuleToAccount` before calling `applyBonusAccrualCheckpoint`, `UpdateLastKnownBonded`, `UpdateLastEventSeq`, or `decrementEventRefCount`.
3. Only advance checkpoints and decrement ref counts after payment succeeds.

Alternatively, in `slashRedelegationPosition`, when `ErrInsufficientBonusPool` is caught, reset `pos` back to its pre-call state (re-read from store) before calling `setPositionWithState`, so that the un-paid segment remains claimable after the pool is replenished.

---

### Proof of Concept

```go
func (s *KeeperSuite) TestSlashRedelegation_InsufficientPool_BonusLost() {
    lockAmount := sdkmath.NewInt(sdk.DefaultPowerReduction.Int64())
    _, bondDenom := s.getStakingData()
    // Do NOT fund the rewards pool.

    pos, _, unbondingID := s.setupRedelegatingPosition(lockAmount)
    owner := sdk.MustAccAddressFromBech32(pos.Owner)

    // Advance time so bonus accrues.
    s.ctx = s.ctx.WithBlockTime(s.ctx.BlockTime().Add(30 * 24 * time.Hour))

    balBefore := s.app.BankKeeper.GetBalance(s.ctx, owner, bondDenom)

    // Fire BeforeRedelegationSlashed with empty pool — should swallow ErrInsufficientBonusPool.
    sharesToUnbond := pos.Delegation.Shares.Quo(sdkmath.LegacyNewDec(2))
    err := s.keeper.Hooks().BeforeRedelegationSlashed(s.ctx, unbondingID, sharesToUnbond)
    s.Require().NoError(err) // no error returned — pool error swallowed

    // No bonus paid.
    balAfterSlash := s.app.BankKeeper.GetBalance(s.ctx, owner, bondDenom)
    s.Require().True(balAfterSlash.Amount.Equal(balBefore.Amount), "no bonus paid during slash")

    // Now refund the pool.
    s.fundRewardsPool(sdkmath.NewInt(1_000_000_000), bondDenom)

    // Claim rewards — pre-slash segment should be recoverable but is NOT.
    msgServer := keeper.NewMsgServerImpl(s.keeper)
    resp, err := msgServer.ClaimTierRewards(s.ctx, &types.MsgClaimTierRewards{
        Owner:       pos.Owner,
        PositionIds: []uint64{pos.Id},
    })
    s.Require().NoError(err)

    // BUG: bonus is zero because LastBonusAccrual was already advanced to slash time.
    s.Require().True(resp.BonusRewards.IsZero(),
        "pre-slash bonus segment is permanently lost: %s", resp.BonusRewards)
}
```

The test demonstrates that after the pool is refunded, `ClaimTierRewards` returns zero bonus for the pre-slash segment, confirming permanent loss.

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

**File:** doc/architecture/adr-006.md (L349-349)
```markdown
- Pool balance is never exceeded. User paths fail atomically; the `BeforeRedelegationSlashed` hook forfeits bonus silently if the pool is short, to avoid chain halt.
```

**File:** x/tieredrewards/keeper/bonus_rewards.go (L48-61)
```go
func (k Keeper) sufficientBonusPoolBalance(ctx context.Context, bonus sdk.Coins) error {
	if bonus.IsZero() {
		return nil
	}

	poolAddr := k.accountKeeper.GetModuleAddress(types.RewardsPoolName)
	poolBalance := k.bankKeeper.GetAllBalances(ctx, poolAddr)
	if !poolBalance.IsAllGTE(bonus) {
		return errorsmod.Wrapf(types.ErrInsufficientBonusPool,
			"bonus: %s, pool balance: %s",
			bonus.String(), poolBalance.String())
	}

	return nil
```

**File:** x/tieredrewards/keeper/abci.go (L96-111)
```go
	poolAddr := k.accountKeeper.GetModuleAddress(types.RewardsPoolName)
	poolBalance := k.bankKeeper.GetBalance(ctx, poolAddr, bondDenom)
	topUpAmount := shortFallAmount
	if poolBalance.Amount.IsZero() {
		k.logger(ctx).Error("base rewards pool is empty, cannot top up validator rewards",
			"shortfall", shortFallAmount.String(),
		)
		return nil
	}
	if poolBalance.Amount.LT(shortFallAmount) {
		k.logger(ctx).Error("base rewards pool has insufficient funds, distributing remaining balance",
			"shortfall", shortFallAmount.String(),
			"pool_balance", poolBalance.Amount.String(),
		)
		topUpAmount = poolBalance.Amount
	}
```
