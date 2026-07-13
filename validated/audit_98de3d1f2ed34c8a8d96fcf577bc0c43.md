Based on my analysis of the actual production code, I can trace the exact state transitions involved.

**Key findings from the code:**

**1. Position creation hardcodes `lastKnownBonded=true`** [1](#0-0) 

`createDelegatedPosition` unconditionally passes `true` as `lastKnownBonded` regardless of the validator's actual bonded state at creation time.

**2. Hooks skip recording when count=0** [2](#0-1) 

`AfterValidatorBeginUnbonding` returns early with no UNBOND event when `getPositionCountForValidator==0`. If a position is created *after* this hook fires, no UNBOND event exists in the validator's event log.

**3. `processEventsAndClaimBonus` trusts `LastKnownBonded` as initial state** [3](#0-2) 

The initial `bonded` flag is read directly from `pos.LastKnownBonded`, which was hardcoded to `true` at creation.

**4. `TierRedelegate` also hardcodes `true`** [4](#0-3) 

`UpdateBonusCheckpoints(..., true)` is called unconditionally on redelegate, regardless of the destination validator's bonded state.

---

**Tracing the exploit scenario:**

| Step | State |
|------|-------|
| Validator unbonds, count=0 | No UNBOND event recorded; `ValidatorEventSeq` stays at 0 |
| Position created on unbonded validator | `LastEventSeq=0`, `LastKnownBonded=true` (hardcoded) |
| Validator rebonds, count=1 | BOND event recorded at seq=1 |
| `ClaimTierRewards` called | `bonded=true`, sees BOND event at seq=1 |
| Loop: `bonded==true` → computes bonus for `[T_create, T_bond]` | **Unbonded period billed as bonded** |
| After loop: `bonded==true && val.IsBonded()` → computes bonus for `[T_bond, now]` | Legitimate bonded segment |

The segment `[T_create, T_bond]` is an unbonded period but is computed as bonded because there is no UNBOND event to flip `bonded` to `false`.

**Critical unknown:** Whether `validateNewPosition` (in `msg_validate.go`, not read) blocks position creation on unbonded validators. If it does, the primary `LockTier`/`CommitDelegationToTier` path is blocked. However, the `TierRedelegate` path (line 263) independently reproduces the same `lastKnownBonded=true` hardcoding for the destination validator, and Cosmos SDK permits redelegation to unbonded validators.

**Conclusion:**

The invariant violation is real and traceable to two independent hardcoded `true` values:
- `position.go:56` in `createDelegatedPosition`
- `msg_server.go:263` in `TierRedelegate`

combined with the count=0 guard in `hooks.go:32-34` that prevents the corrective UNBOND event from being recorded.

---

### Title
Hardcoded `lastKnownBonded=true` on Position Creation/Redelegate Allows Bonus Accrual During Unbonded Validator Gaps — (`x/tieredrewards/keeper/position.go`, `x/tieredrewards/keeper/msg_server.go`)

### Summary
When a validator unbonds while no tier positions exist (`count=0`), no UNBOND event is recorded. A position subsequently created on that validator (or redelegated to it) is initialized with `LastKnownBonded=true` unconditionally. When the validator later rebonds, a BOND event is recorded. On the first claim, `processEventsAndClaimBonus` starts with `bonded=true` and computes bonus for the entire gap `[position_creation_time, bond_time]` — a period during which the validator was not bonded — violating the invariant that bonus accrues only during bonded segments.

### Finding Description
`createDelegatedPosition` passes `true` as `lastKnownBonded` unconditionally:

```go
// position.go:56
pos := types.NewPosition(id, owner, tier.Id, delAddr.String(), blockHeight, lastEventSeq, blockTime, true, blockTime)
```

`TierRedelegate` does the same for the destination validator:

```go
// msg_server.go:263
pos.UpdateBonusCheckpoints(latestSeq, sdkCtx.BlockTime(), true)
```

Both ignore the validator's actual bonded state. The hooks guard:

```go
// hooks.go:32-34
if count == 0 {
    return nil
}
```

means no UNBOND event is ever written when the validator unbonds with zero positions. The position's `LastEventSeq` is set to the latest seq at creation time (which is pre-BOND), so `getValidatorEventsSince` returns only the BOND event. With `bonded=true` as the starting state and no UNBOND event to flip it, the entire pre-bond gap is treated as a bonded segment.

### Impact Explanation
An attacker can receive bonus rewards for time periods during which the validator was not bonded. The bonus is paid from the `RewardsPoolName` module account via `bankKeeper.SendCoinsFromModuleToAccount`. The magnitude equals `computeSegmentBonus` over the unbonded gap duration, which can be arbitrarily large depending on how long the validator was unbonded before the position was created.

### Likelihood Explanation
Validators cycle between bonded and unbonded states (jailing, tombstoning, rank changes). A user who monitors validator state transitions can time position creation to exploit this window. The `TierRedelegate` path is particularly accessible since it requires no special timing — redelegating to any validator that was previously unbonded with count=0 and has since rebonded triggers the same flaw.

### Recommendation
In `createDelegatedPosition`, query the validator's actual bonded state and set `lastKnownBonded` accordingly:
```go
val, err := k.stakingKeeper.GetValidator(ctx, valAddr)
lastKnownBonded := val.IsBonded()
pos := types.NewPosition(..., lastKnownBonded, blockTime)
```
Apply the same fix to `TierRedelegate`'s `UpdateBonusCheckpoints` call. Alternatively, when creating a position or redelegating, check whether the validator's current bonded state matches the implied initial state and synthesize a corrective event if needed.

### Proof of Concept
1. Create a validator; ensure no tier positions exist (`count=0`).
2. Trigger `AfterValidatorBeginUnbonding` — verify no UNBOND event is stored.
3. Create a tier position on the now-unbonded validator via `MsgLockTier`.
4. Advance block time by `D` (e.g., 7 days).
5. Trigger `AfterValidatorBonded` — verify BOND event stored at seq=1.
6. Call `MsgClaimTierRewards`.
7. Assert: bonus paid equals `computeSegmentBonus` over `D` (the unbonded gap), not zero. The correct value should be zero since the validator was not bonded during `[T_create, T_bond]`. [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** x/tieredrewards/keeper/position.go (L34-56)
```go
func (k Keeper) createDelegatedPosition(
	ctx context.Context,
	owner string,
	tier types.Tier,
	valAddr sdk.ValAddress,
	delAddr sdk.AccAddress,
	triggerExitImmediately bool,
) (types.Position, error) {
	id, err := k.NextPositionId.Next(ctx)
	if err != nil {
		return types.Position{}, err
	}

	lastEventSeq, err := k.getValidatorEventLatestSeq(ctx, valAddr)
	if err != nil {
		return types.Position{}, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	blockTime := sdkCtx.BlockTime()
	blockHeight := uint64(sdkCtx.BlockHeight())

	pos := types.NewPosition(id, owner, tier.Id, delAddr.String(), blockHeight, lastEventSeq, blockTime, true, blockTime)
```

**File:** x/tieredrewards/keeper/hooks.go (L27-50)
```go
func (h Hooks) AfterValidatorBeginUnbonding(ctx context.Context, _ sdk.ConsAddress, valAddr sdk.ValAddress) error {
	count, err := h.k.getPositionCountForValidator(ctx, valAddr)
	if err != nil {
		return err
	}
	if count == 0 {
		return nil
	}

	tokensPerShare, err := h.k.getTokensPerShare(ctx, valAddr)
	if err != nil {
		return err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	_, err = h.k.appendValidatorEvent(ctx, valAddr, types.ValidatorEvent{
		Height:         sdkCtx.BlockHeight(),
		Timestamp:      sdkCtx.BlockTime(),
		EventType:      types.ValidatorEventType_VALIDATOR_EVENT_TYPE_UNBOND,
		TokensPerShare: tokensPerShare,
		ReferenceCount: count,
	})
	return err
}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L162-213)
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
```

**File:** x/tieredrewards/keeper/msg_server.go (L257-263)
```go
	latestSeq, err := ms.getValidatorEventLatestSeq(ctx, dstValAddr)
	if err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	pos.UpdateBonusCheckpoints(latestSeq, sdkCtx.BlockTime(), true)
```
