### Title
Hardcoded `LastKnownBonded = true` in `MsgTierRedelegate` enables bonus reward overclaim for unbonded-validator periods — (File: `x/tieredrewards/keeper/msg_server.go`)

---

### Summary

After `MsgTierRedelegate`, the position's `LastKnownBonded` checkpoint is unconditionally set to `true` regardless of the destination validator's actual bonded state. If the destination validator is currently unbonded, the lazy bonus replay in `processEventsAndClaimBonus` will incorrectly treat the post-redelegate period as bonded, paying out bonus rewards from the `RewardsPoolName` module account for time the validator was not bonded.

---

### Finding Description

In `MsgTierRedelegate`, after claiming rewards and completing the redelegation, the position's bonus checkpoints are updated with a hardcoded `true` for `LastKnownBonded`: [1](#0-0) 

```go
pos.UpdateBonusCheckpoints(latestSeq, sdkCtx.BlockTime(), true)
```

`LastKnownBonded` is the persisted bonded state used as the starting point for the next lazy bonus replay. Its semantics are defined in `UpdateBonusCheckpoints`: [2](#0-1) 

The value is set to `true` unconditionally, regardless of whether the destination validator is actually bonded at redelegate time.

The lazy replay in `processEventsAndClaimBonus` initialises `bonded` from `pos.LastKnownBonded`: [3](#0-2) 

It then walks events since `pos.LastEventSeq`. For each event, if `bonded == true` it computes bonus for the preceding segment using the event's `tokensPerShare` snapshot: [4](#0-3) 

**The attack path:**

1. Validator B is currently unbonded (an UNBOND event was appended at seq=5 for validator B).
2. User calls `MsgTierRedelegate` to move their position to validator B.
   - `latestSeq = 5` (latest seq for validator B — the UNBOND event is skipped by the `StartExclusive` range in `getValidatorEventsSince`)
   - `LastKnownBonded = true` (hardcoded — **incorrect**, validator B is unbonded)
   - `LastBonusAccrual = T1`
3. At time T2, validator B becomes bonded. `AfterValidatorBonded` appends a BOND event at seq=6 with `tokensPerShare = R`.
4. User calls `MsgClaimTierRewards` at time T3.
   - `processEventsAndClaimBonus` starts with `bonded = true`, `segmentStart = T1`
   - Processes BOND event (seq=6): `bonded == true` → computes bonus for segment `[T1, T2]` using `tokensPerShare = R`
   - **Bonus = `shares × R × bonusApy × (T2−T1) / SecondsPerYear`** — paid for the unbonded period, which should be zero.

The `validateRedelegatePosition` function does not require the destination validator to be bonded. The ADR reference table for `MsgTierRedelegate` lists only: "Exit not elapsed; tier not close-only; Amount > 0; dst != src" — no bonded-validator check: [5](#0-4) 

The `AfterValidatorBonded` hook that records the BOND event also uses `getTokensPerShare` at bond time, which will be non-zero for a validator that was jailed/unbonding but not fully slashed: [6](#0-5) 

The final-segment guard `bonded && val.IsBonded()` only protects the tail segment after all events are processed: [7](#0-6) 

It does **not** protect intermediate segments computed while walking events, which is where the overclaim occurs.

---

### Impact Explanation

The corrupted value is the `RewardsPoolName` module account balance: bonus tokens are transferred out of the pool for a period during which the validator was unbonded and no bonus should have accrued. A user with a large tiered position can redelegate to a temporarily-unbonded validator and drain the rewards pool by the amount `shares × tokensPerShare × bonusApy × unbondedDuration / SecondsPerYear`. Other legitimate position holders who later claim rewards may find the pool insufficient and be blocked (the `ErrInsufficientBonusPool` path fails atomically for user-driven paths). [8](#0-7) 

**Impact: High** — direct fund loss from the rewards pool module account.

---

### Likelihood Explanation

**Likelihood: Low.** The attacker must hold a tiered position, identify a validator that is currently unbonded but expected to re-bond (e.g., after unjailing), and redelegate to it before the BOND event is recorded. This is an unusual but fully valid on-chain action requiring no privileged access — only a standard `MsgTierRedelegate` transaction signed by the position owner.

---

### Recommendation

Replace the hardcoded `true` with the actual bonded state of the destination validator at redelegate time:

```go
dstVal, err := ms.stakingKeeper.GetValidator(ctx, dstValAddr)
if err != nil {
    return nil, err
}
pos.UpdateBonusCheckpoints(latestSeq, sdkCtx.BlockTime(), dstVal.IsBonded())
```

This mirrors the invariant enforced at position creation (`MsgLockTier` requires the validator to be bonded, so `LastKnownBonded = true` is correct there) and ensures the lazy replay starts from the correct bonded state after every redelegate. [9](#0-8) 

---

### Proof of Concept

```
State before:
  Validator B: unbonded, ValidatorEventSeq[B] = 5 (UNBOND event at seq=5, time=T0)

Step 1 — MsgTierRedelegate (owner → validator B, time=T1):
  claimRewards(pos)                          // settles rewards up to T1 on old validator
  redelegate(posDelAddr, srcVal, B, shares)  // SDK redelegate, no bonded check on dst
  latestSeq = getValidatorEventLatestSeq(B)  // = 5
  pos.UpdateBonusCheckpoints(5, T1, true)    // LastKnownBonded = true  ← BUG

Step 2 — AfterValidatorBonded fires for B at time T2 (T2 > T1):
  appendValidatorEvent(B, BOND, tokensPerShare=R, seq=6)

Step 3 — MsgClaimTierRewards at time T3:
  processEventsAndClaimBonus:
    bonded      = true   (from LastKnownBonded — incorrect)
    segmentStart = T1
    events = [BOND @ T2, seq=6]

    entry = BOND @ T2:
      bonded == true  →  bonus += computeSegmentBonus([T1,T2], R)
                         = shares × R × bonusApy × (T2−T1) / SecondsPerYear  ← OVERCLAIM
      bonded = true (BOND event)
      segmentStart = T2

    final segment [T2, T3]: bonded && val.IsBonded() → correct bonus

  SendCoinsFromModuleToAccount(RewardsPoolName, owner, overclaim + correct)
```

The overclaim `shares × R × bonusApy × (T2−T1) / SecondsPerYear` is paid from the `RewardsPoolName` module account for the period `[T1, T2]` during which validator B was unbonded and no bonus should have been earned.

### Citations

**File:** x/tieredrewards/keeper/msg_server.go (L210-230)
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
```

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

**File:** x/tieredrewards/keeper/claim_rewards.go (L163-165)
```go
	// This prevents overpaying bonus for unbonded gaps between claims.
	bonded := pos.LastKnownBonded
	segmentStart := pos.LastBonusAccrual
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L172-199)
```go
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
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L201-213)
```go
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

**File:** x/tieredrewards/keeper/claim_rewards.go (L230-232)
```go
	if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/hooks.go (L53-76)
```go
func (h Hooks) AfterValidatorBonded(ctx context.Context, _ sdk.ConsAddress, valAddr sdk.ValAddress) error {
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
		EventType:      types.ValidatorEventType_VALIDATOR_EVENT_TYPE_BOND,
		TokensPerShare: tokensPerShare,
		ReferenceCount: count,
	})
	return err
}
```
