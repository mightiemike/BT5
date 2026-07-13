### Title
`LastKnownBonded` Hardcoded to `true` in `TierRedelegate` Enables Bonus Reward Overpayment — (File: `x/tieredrewards/keeper/msg_server.go`)

---

### Summary

In `TierRedelegate`, `UpdateBonusCheckpoints` is called with `lastKnownBonded = true` unconditionally, regardless of whether the destination validator is actually bonded at redelegate time. When a user redelegates to a non-jailed unbonding validator, this incorrect checkpoint causes `processEventsAndClaimBonus` to compute bonus rewards for the entire unbonding period on the next claim, draining the `RewardsPoolName` module account.

---

### Finding Description

In `msg_server.go`, after a successful redelegate, the handler advances the position's bonus checkpoints:

```go
latestSeq, err := ms.getValidatorEventLatestSeq(ctx, dstValAddr)
...
pos.UpdateBonusCheckpoints(latestSeq, sdkCtx.BlockTime(), true)
``` [1](#0-0) 

The third argument — `lastKnownBonded` — is hardcoded to `true` unconditionally. `UpdateBonusCheckpoints` writes this directly into the persisted `Position.LastKnownBonded` field:

```go
func (p *Position) UpdateBonusCheckpoints(lastEventSeq uint64, t time.Time, lastKnownBonded bool) {
    p.LastEventSeq = lastEventSeq
    p.LastBonusAccrual = t
    p.LastKnownBonded = lastKnownBonded
}
``` [2](#0-1) 

`LastKnownBonded` is the **starting bonded state** for every subsequent bonus computation. In `processEventsAndClaimBonus`:

```go
bonded := pos.LastKnownBonded   // ← starts as true regardless of dst validator state
segmentStart := pos.LastBonusAccrual
...
for _, entry := range events {
    if bonded {
        bonus := k.computeSegmentBonus(*pos, tier, segmentStart, evt.Timestamp, evt.TokensPerShare)
        totalBonus = totalBonus.Add(bonus)
    }
    switch evt.EventType {
    case UNBOND: bonded = false
    case BOND:   bonded = true
    ...
    }
    segmentStart = evt.Timestamp
}
``` [3](#0-2) 

If the destination validator is **unbonding** (not jailed, just outside the active set) at redelegate time:

1. `latestSeq` is set to the latest event seq for the destination validator — which is the seq of the UNBOND event that kicked it out of the active set.
2. `LastKnownBonded = true` is persisted, even though the validator is unbonding.
3. When the validator eventually bonds again, a BOND event is recorded with `seq = latestSeq + 1`.
4. On the next `ClaimTierRewards`, `processEventsAndClaimBonus` processes the BOND event with `bonded = true` as the starting state, computing bonus for the entire segment `[redelegate_time, bond_time]` — a period when the validator was actually unbonding and no bonus should accrue.

The ADR-006 specification for `MsgTierRedelegate` lists validations as: "Exit not elapsed; tier not close-only; Amount > 0; dst != src" — **no "validator bonded" check for the destination**, unlike `MsgLockTier` and `MsgExitTierWithDelegation` which both explicitly require the validator to be bonded. [4](#0-3) 

The Cosmos SDK's `BeginRedelegation` does not require the destination validator to be bonded (only that it is not jailed), so the staking layer does not block this path.

---

### Impact Explanation

The corrupted value is `Position.LastKnownBonded` in the `Positions` collection, which causes `processEventsAndClaimBonus` to compute and pay out bonus rewards from the `RewardsPoolName` module account for a period when no bonus was owed. The direct financial impact is:

- **Unauthorized extraction of bonus tokens** from the `RewardsPoolName` module account.
- **Denial of legitimate claims**: other users with valid accrued bonus rewards may hit `ErrInsufficientBonusPool` and be unable to claim. [5](#0-4) 

---

### Likelihood Explanation

Validators regularly transition between bonded and unbonding states on Cosmos chains (kicked out of the active set due to low voting power, voluntary unbonding, etc.) without being jailed. Any user with a tier position can observe an unbonding non-jailed validator and call `MsgTierRedelegate` to it. The attack is repeatable across multiple validators and positions.

---

### Recommendation

Replace the hardcoded `true` with the actual bonded state of the destination validator at redelegate time:

```go
dstVal, err := ms.stakingKeeper.GetValidator(ctx, dstValAddr)
if err != nil {
    return nil, err
}
pos.UpdateBonusCheckpoints(latestSeq, sdkCtx.BlockTime(), dstVal.IsBonded())
``` [6](#0-5) 

---

### Proof of Concept

1. Validator B is non-jailed and transitions to unbonding (kicked from active set). An UNBOND event is recorded with `seq = N`.
2. User holds a tier position on validator A (bonded).
3. User calls `MsgTierRedelegate` with `DstValidator = B`:
   - `claimRewards` settles all rewards up to block time `T`.
   - `getValidatorEventLatestSeq(dstValAddr)` returns `N`.
   - `UpdateBonusCheckpoints(N, T, true)` persists `LastKnownBonded = true`, `LastBonusAccrual = T`, `LastEventSeq = N`.
4. Validator B is unjailed/re-enters active set at time `T + 30d`. A BOND event is recorded with `seq = N+1`.
5. User calls `MsgClaimTierRewards`:
   - `processEventsAndClaimBonus` fetches events since seq `N`, finds the BOND event at `N+1`.
   - `bonded = true` (from `LastKnownBonded`).
   - Computes bonus for segment `[T, T+30d]` with `bonded = true` — 30 days of bonus rewards paid out.
   - Validator B was unbonding during this entire period; zero bonus should have accrued.
6. User receives 30 days of unearned bonus tokens from `RewardsPoolName`. [7](#0-6) [8](#0-7)

### Citations

**File:** x/tieredrewards/keeper/msg_server.go (L210-283)
```go
func (ms msgServer) TierRedelegate(ctx context.Context, msg *types.MsgTierRedelegate) (*types.MsgTierRedelegateResponse, error) {
	if err := msg.Validate(); err != nil {
		return nil, err
	}

	pos, err := ms.getPositionState(ctx, msg.PositionId)
	if err != nil {
		return nil, err
	}

	if err := ms.validateRedelegatePosition(ctx, pos, msg.Owner, msg.DstValidator); err != nil {
		return nil, err
	}

	dstValAddr, err := sdk.ValAddressFromBech32(msg.DstValidator)
	if err != nil {
		return nil, err
	}

	pos, _, _, err = ms.claimRewards(ctx, pos)
	if err != nil {
		return nil, err
	}

	srcValidator := pos.Delegation.ValidatorAddress
	srcValAddr, err := sdk.ValAddressFromBech32(srcValidator)
	if err != nil {
		return nil, err
	}

	delAddr, err := sdk.AccAddressFromBech32(pos.DelegatorAddress)
	if err != nil {
		return nil, errorsmod.Wrap(sdkerrors.ErrInvalidAddress, "invalid delegator address")
	}

	completionTime, unbondingID, err := ms.redelegate(ctx, delAddr, srcValAddr, dstValAddr, pos.Delegation.Shares)
	if err != nil {
		return nil, err
	}
	// unbondingID 0 means the source validator is unbonded; redelegation is instant.
	// We skip mapping because no asynchronous completion hook will trigger.
	if unbondingID != 0 {
		if err := ms.setRedelegationMapping(ctx, unbondingID, pos.Id); err != nil {
			return nil, err
		}
	}

	latestSeq, err := ms.getValidatorEventLatestSeq(ctx, dstValAddr)
	if err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	pos.UpdateBonusCheckpoints(latestSeq, sdkCtx.BlockTime(), true)

	if err := ms.setPosition(ctx, pos.Position, &ValidatorTransition{PreviousAddress: srcValidator}); err != nil {
		return nil, err
	}

	if err := sdkCtx.EventManager().EmitTypedEvent(&types.EventPositionRedelegated{
		PositionId:     pos.Id,
		TierId:         pos.TierId,
		Owner:          pos.Owner,
		SrcValidator:   srcValidator,
		DstValidator:   msg.DstValidator,
		CompletionTime: completionTime,
	}); err != nil {
		return nil, err
	}

	return &types.MsgTierRedelegateResponse{
		CompletionTime: completionTime,
		PositionId:     pos.Id,
	}, nil
```

**File:** x/tieredrewards/types/position.go (L65-69)
```go
func (p *Position) UpdateBonusCheckpoints(lastEventSeq uint64, t time.Time, lastKnownBonded bool) {
	p.LastEventSeq = lastEventSeq
	p.LastBonusAccrual = t
	p.LastKnownBonded = lastKnownBonded
}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L162-217)
```go
	// Use the persisted bonded state from the last replay, not a hardcoded default.
	// This prevents overpaying bonus for unbonded gaps between claims.
	bonded := pos.LastKnownBonded
	segmentStart := pos.LastBonusAccrual

	tier, err := k.getTier(ctx, pos.TierId)
	if err != nil {
		return nil, err
	}

	for _, entry := range events {
		evt := entry.Event

		if bonded {
			// Compute bonus for the bonded segment [segmentStart, eventTime]
			// using the snapshot rate at the event.
			bonus := k.computeSegmentBonus(*pos, tier, segmentStart, evt.Timestamp, evt.TokensPerShare)
			totalBonus = totalBonus.Add(bonus)
		}

		// Update bonded state based on event type.
		switch evt.EventType {
		case types.ValidatorEventType_VALIDATOR_EVENT_TYPE_UNBOND:
			bonded = false
		case types.ValidatorEventType_VALIDATOR_EVENT_TYPE_BOND:
			bonded = true
		case types.ValidatorEventType_VALIDATOR_EVENT_TYPE_SLASH:
			// Slash doesn't change bonded state.
		}

		segmentStart = evt.Timestamp
		pos.UpdateLastEventSeq(entry.Seq)

		// Decrement reference count.
		if err := k.decrementEventRefCount(ctx, valAddr, entry.Seq); err != nil {
			return nil, err
		}
	}

	val, err := k.stakingKeeper.GetValidator(ctx, valAddr)
	if err != nil {
		return nil, err
	}
	// Defensive: validator bond status check
	if bonded && val.IsBonded() {
		currentRate, err := k.getTokensPerShare(ctx, valAddr)
		if err != nil {
			return nil, err
		}
		bonus := k.computeSegmentBonus(*pos, tier, segmentStart, blockTime, currentRate)
		totalBonus = totalBonus.Add(bonus)
	}

	applyBonusAccrualCheckpoint(&pos.Position, blockTime)
	// Persist the bonded state so the next replay starts correctly.
	pos.UpdateLastKnownBonded(bonded)
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L228-241)
```go
	bonusCoins := sdk.NewCoins(sdk.NewCoin(bondDenom, totalBonus))

	if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
		return nil, err
	}

	ownerAddr, err := sdk.AccAddressFromBech32(pos.Owner)
	if err != nil {
		return nil, errorsmod.Wrap(sdkerrors.ErrInvalidAddress, "invalid owner address")
	}

	if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.RewardsPoolName, ownerAddr, bonusCoins); err != nil {
		return nil, err
	}
```

**File:** doc/architecture/adr-006.md (L155-168)
```markdown
| Message | Description | Key Validations |
|---------|-------------|-----------------|
| **MsgLockTier** | Lock tokens + delegate to validator. Optional `trigger_exit_immediately`. | amount >= MinLockAmount; validator bonded; tier not close-only; **owner is not a vesting account** |
| **MsgCommitDelegationToTier** | Transfer existing delegation to tier (no unbonding). Partial allowed. | amount <= user's delegation; amount >= MinLockAmount; tier not close-only; **delegator is not a vesting account** |
| **MsgAddToTierPosition** | Add tokens to existing position. Claims rewards first and delegates new amount. | Delegated; not exiting; tier not close-only |
| **MsgTierRedelegate** | Move delegation to another validator. Claims rewards first. | Exit not elapsed; tier not close-only; Amount > 0; dst != src |
| **MsgTierUndelegate** | Undelegate after exit commitment. Claims rewards first. Clears delegation state immediately. | Exit triggered; exit elapsed; delegated |
| **MsgTriggerExitFromTier** | Start exit commitment. | Not already exiting |
| **MsgClearPosition** | Cancel exit. Settles rewards first. If delegated, resets `LastBonusAccrual` to block_time. No-op if not exiting. | Tier not close-only; if exit elapsed: must be delegated and not unbonding |
| **MsgWithdrawFromTier** | Withdraw tokens + delete position. | Exit triggered; exit elapsed; not delegated; no pending unbonding |
| **MsgClaimTierRewards** | Claim base + bonus rewards for one or more positions. All positions must belong to the signer. | Owner match on all positions; position_ids non-empty, no duplicates, max 500; returns zero per position if not delegated |
| **MsgExitTierWithDelegation** | Transfer delegation back to owner (no unbonding). Supports partial exits. Deletes position if fully exited. | Exit triggered; exit elapsed; delegated; amount > 0; amount <= position amount; validator bonded; no active redelegation; partial exit: remaining >= MinLockAmount |
| **MsgUpdateParams** | Update module params. | Authority (gov) |
| **MsgAddTier** / **MsgUpdateTier** / **MsgDeleteTier** | Manage tiers. DeleteTier fails if positions exist. | Authority (gov) |
```
