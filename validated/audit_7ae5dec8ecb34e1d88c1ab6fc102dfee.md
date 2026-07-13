### Title
Hardcoded `LastKnownBonded = true` on Redelegate Allows Bonus Accrual During Unbonded Period - (`x/tieredrewards/keeper/msg_server.go`)

---

### Summary

`MsgTierRedelegate` unconditionally sets `LastKnownBonded = true` on the position after moving it to a destination validator, regardless of whether that validator is currently bonded. When the destination validator is unbonded at the time of redelegation, the position's bonus-accrual state is corrupted: the next `processEventsAndClaimBonus` call treats the entire gap from redelegation time to the validator's next BOND event as a bonded segment and pays out bonus rewards for it, even though the validator was unbonded throughout that period. This drains the rewards pool at the expense of other users.

---

### Finding Description

In `TierRedelegate`, after settling rewards and moving the delegation, the position's bonus checkpoints are reset:

```go
pos.UpdateBonusCheckpoints(latestSeq, sdkCtx.BlockTime(), true)
``` [1](#0-0) 

The third argument (`true`) hardcodes `LastKnownBonded = true` unconditionally. `UpdateBonusCheckpoints` persists this directly into the position:

```go
func (p *Position) UpdateBonusCheckpoints(lastEventSeq uint64, t time.Time, lastKnownBonded bool) {
    p.LastEventSeq = lastEventSeq
    p.LastBonusAccrual = t
    p.LastKnownBonded = lastKnownBonded
}
``` [2](#0-1) 

At claim time, `processEventsAndClaimBonus` initialises its bonded-state variable directly from this persisted field:

```go
bonded := pos.LastKnownBonded
segmentStart := pos.LastBonusAccrual
``` [3](#0-2) 

The event-replay loop then computes bonus for every segment where `bonded == true`:

```go
for _, entry := range events {
    evt := entry.Event
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
``` [4](#0-3) 

`latestSeq` is set to the destination validator's current latest event sequence number:

```go
latestSeq, err := ms.getValidatorEventLatestSeq(ctx, dstValAddr)
``` [5](#0-4) 

If the destination validator is currently unbonded, the UNBOND event that caused it to be unbonded has a sequence number ≤ `latestSeq` and is therefore **skipped** by `getValidatorEventsSince` (which returns only events with seq > `startSeq`). No corrective UNBOND event exists in the future event log. The position therefore starts its next accrual window with `LastKnownBonded = true` and `LastBonusAccrual = redelegation_time`, with no pending event to flip `bonded` to `false`.

When the validator later rebonds, a BOND event is appended. On the next claim, the loop processes this BOND event with `bonded = true`, so it computes bonus for `[redelegation_time, BOND_event_time]` — the entire unbonded gap — and pays it out.

---

### Impact Explanation

A user who redelegates to an unbonded validator receives bonus rewards for the full period the validator was unbonded (from redelegation time to the validator's next BOND event). This is a direct, incorrect transfer of tokens from the shared rewards pool (`RewardsPoolName`) to the attacker. Other users' bonus claims are unaffected individually, but the pool balance is reduced, potentially causing legitimate claims to fail with `ErrInsufficientBonusPool`.

The corrupted value is: `pos.LastKnownBonded` (set to `true` when it should be `false`), which causes `processEventsAndClaimBonus` to compute a non-zero `totalBonus` for a segment during which no bonus should accrue, and then execute:

```go
k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.RewardsPoolName, ownerAddr, bonusCoins)
``` [6](#0-5) 

---

### Likelihood Explanation

Any delegator with a tier position can trigger this by sending `MsgTierRedelegate` targeting a currently-unbonded (jailed) validator. Jailed validators are a normal, recurring network condition. No privileged access is required. The attacker only needs to observe the validator set, pick an unbonded validator, and redelegate. The exploit is repeatable: after the validator rebonds, the attacker can redelegate away and back again to repeat the cycle.

---

### Recommendation

Replace the hardcoded `true` in `TierRedelegate` with a live check of the destination validator's bonded state:

```go
dstVal, err := ms.stakingKeeper.GetValidator(ctx, dstValAddr)
if err != nil {
    return nil, err
}
pos.UpdateBonusCheckpoints(latestSeq, sdkCtx.BlockTime(), dstVal.IsBonded())
```

This mirrors the existing pattern in `processEventsAndClaimBonus`, which reads `val.IsBonded()` before computing the final tail segment. [7](#0-6) 

---

### Proof of Concept

1. Validator `V` is jailed and transitions to unbonded. A UNBOND event is appended at seq `N`.
2. User `U` holds a tier position on a bonded validator `V0`.
3. `U` sends `MsgTierRedelegate` targeting `V`. `claimRewards` settles all pending bonus up to `T_redelegate`. `latestSeq = N`. `pos.UpdateBonusCheckpoints(N, T_redelegate, true)` is written.
4. `V` is unjailed and rebonds at time `T_bond`. A BOND event is appended at seq `N+1`.
5. `U` sends `MsgClaimTierRewards`. `processEventsAndClaimBonus` starts with `bonded = true`, `segmentStart = T_redelegate`. It processes seq `N+1` (BOND event): since `bonded = true`, it computes `bonus = computeSegmentBonus(pos, tier, T_redelegate, T_bond, rate_at_bond)` and adds it to `totalBonus`. This covers the entire unbonded gap `[T_redelegate, T_bond]`.
6. `U` receives bonus tokens for a period during which `V` was unbonded and no bonus should have accrued. [8](#0-7) [9](#0-8)

### Citations

**File:** x/tieredrewards/keeper/msg_server.go (L257-265)
```go
	latestSeq, err := ms.getValidatorEventLatestSeq(ctx, dstValAddr)
	if err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	pos.UpdateBonusCheckpoints(latestSeq, sdkCtx.BlockTime(), true)

	if err := ms.setPosition(ctx, pos.Position, &ValidatorTransition{PreviousAddress: srcValidator}); err != nil {
```

**File:** x/tieredrewards/types/position.go (L65-69)
```go
func (p *Position) UpdateBonusCheckpoints(lastEventSeq uint64, t time.Time, lastKnownBonded bool) {
	p.LastEventSeq = lastEventSeq
	p.LastBonusAccrual = t
	p.LastKnownBonded = lastKnownBonded
}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L161-213)
```go
	totalBonus := math.ZeroInt()
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

**File:** x/tieredrewards/keeper/claim_rewards.go (L239-240)
```go
	if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.RewardsPoolName, ownerAddr, bonusCoins); err != nil {
		return nil, err
```
