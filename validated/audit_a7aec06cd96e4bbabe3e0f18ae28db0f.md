### Title
Stale `LastKnownBonded = true` Initialization in `createDelegatedPosition` Allows Excess Bonus Rewards — (`x/tieredrewards/keeper/position.go`)

---

### Summary

When a new tier position is created via `MsgLockTier` or `MsgCommitDelegationToTier`, `createDelegatedPosition` hardcodes `LastKnownBonded = true` regardless of the validator's actual current bonded state. If the target validator is currently unbonded at position-creation time, this stale initialization causes `processEventsAndClaimBonus` to treat the entire unbonded period (from position creation until the validator re-bonds) as a bonded segment, paying out excess bonus rewards from the rewards pool.

---

### Finding Description

`createDelegatedPosition` initialises every new position with `LastKnownBonded = true`: [1](#0-0) 

```go
pos := types.NewPosition(id, owner, tier.Id, delAddr.String(), blockHeight, lastEventSeq, blockTime, true, blockTime)
```

The `true` argument is `lastKnownBonded`. It is unconditional — the validator's live bonded state is never consulted.

`LastEventSeq` is correctly set to the latest event sequence on the validator: [2](#0-1) 

This means the position skips all historical events (including any prior `UNBOND` event that put the validator into an unbonded state). The position's `LastKnownBonded` is therefore the *only* record of the validator's bonded state at creation time — and it is always `true`.

`processEventsAndClaimBonus` uses `LastKnownBonded` as the starting bonded state for the bonus-accrual walk: [3](#0-2) 

```go
bonded := pos.LastKnownBonded   // always true at creation
segmentStart := pos.LastBonusAccrual  // creation block time
```

When the first new event after position creation is a `BOND` event (validator re-bonds), the loop body executes: [4](#0-3) 

```go
if bonded {
    bonus := k.computeSegmentBonus(*pos, tier, segmentStart, evt.Timestamp, evt.TokensPerShare)
    totalBonus = totalBonus.Add(bonus)
}
```

Because `bonded = true` (stale), the segment `[creation_time, bond_event_time]` is treated as a bonded period and bonus is computed for it — even though the validator was unbonded throughout that entire interval.

**Concrete attack path:**

1. Validator V is bonded; an `UNBOND` event is recorded at seq N (e.g., validator is jailed).
2. While V is still unbonded, a user submits `MsgLockTier` (or `MsgCommitDelegationToTier`) targeting V.
   - `lastEventSeq = N` (latest seq, past the UNBOND event).
   - `LastKnownBonded = true` (hardcoded — **wrong**, should be `false`).
   - `LastBonusAccrual = T_create`.
3. V is unjailed; a `BOND` event is recorded at seq N+1, timestamp `T_bond`.
4. User calls `MsgClaimTierRewards`.
   - `events = [BOND event at seq N+1]`.
   - `bonded = true` (stale initial state).
   - Loop: `if bonded` → `computeSegmentBonus(pos, tier, T_create, T_bond, tokensPerShare_at_bond)` → **excess bonus paid for the unbonded period `[T_create, T_bond]`**.
5. Bonus coins are sent from `RewardsPoolName` to the position owner. [5](#0-4) 

---

### Impact Explanation

The position owner receives bonus rewards for a period during which the validator was unbonded and no bonus should have accrued. The excess is drawn directly from the `tieredrewards` rewards pool (`RewardsPoolName`). The magnitude scales with the position's share size, the tier's bonus APY, and the length of the unbonded window between position creation and re-bonding. Repeated exploitation (create position → wait for re-bond → claim → exit → repeat) drains the pool faster than intended.

---

### Likelihood Explanation

Validator jailing and unjailing are routine on-chain events on Cosmos POS chains. Any user who monitors the mempool or chain state can observe a jailed validator and submit `MsgLockTier` targeting it in the same block or shortly after. No privileged access is required — `MsgLockTier` and `MsgCommitDelegationToTier` are standard user-facing messages. The Cosmos SDK `Delegate` call does not reject delegations to unbonded validators.

---

### Recommendation

In `createDelegatedPosition`, replace the hardcoded `true` with the validator's live bonded state:

```go
val, err := k.stakingKeeper.GetValidator(ctx, valAddr)
if err != nil {
    return types.Position{}, err
}
lastKnownBonded := val.IsBonded()

pos := types.NewPosition(id, owner, tier.Id, delAddr.String(), blockHeight, lastEventSeq, blockTime, lastKnownBonded, blockTime)
```

This mirrors the fix applied in `TierRedelegate`, which explicitly fetches the destination validator's latest event seq and sets `LastKnownBonded` to `true` only because it knows the redelegate target must be bonded at that point: [6](#0-5) 

---

### Proof of Concept

```
T=0:  Validator V is bonded. ValidatorEventSeq[V] = 0.
T=1:  V is jailed → AfterValidatorBeginUnbonding fires → UNBOND event recorded at seq=1.
      ValidatorEventSeq[V] = 1.  V.IsBonded() = false.

T=2:  Alice sends MsgLockTier(tier=1, amount=1_000_000, validator=V).
      createDelegatedPosition:
        lastEventSeq = getValidatorEventLatestSeq(V) = 1   ✓ (skips UNBOND)
        LastKnownBonded = true                              ✗ (V is unbonded)
        LastBonusAccrual = T2

T=3:  V is unjailed → AfterValidatorBonded fires → BOND event recorded at seq=2, timestamp=T3.

T=4:  Alice sends MsgClaimTierRewards(positionId=Alice's).
      processEventsAndClaimBonus:
        bonded = pos.LastKnownBonded = true   ← stale
        segmentStart = T2
        events = [BOND event seq=2, timestamp=T3]
        Loop iteration (BOND event):
          if bonded (true) → computeSegmentBonus(T2, T3, tokensPerShare@T3)
          → bonus = shares * tokensPerShare * APY * (T3-T2) / SecondsPerYear
          → PAID OUT — but V was unbonded during [T2, T3], no bonus should accrue
        bonded = true, segmentStart = T3
        Final segment: bonded && V.IsBonded() → correct bonus for [T3, T4]

Result: Alice receives bonus for the full interval [T2, T4] instead of only [T3, T4].
        Excess = shares * tokensPerShare * APY * (T3-T2) / SecondsPerYear
        drawn from RewardsPoolName.
``` [7](#0-6) [8](#0-7)

### Citations

**File:** x/tieredrewards/keeper/position.go (L34-72)
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

	ownerAddr, err := sdk.AccAddressFromBech32(owner)
	if err != nil {
		return types.Position{}, err
	}

	if err := k.routeBaseRewardsToOwner(ctx, delAddr, ownerAddr); err != nil {
		return types.Position{}, err
	}

	if triggerExitImmediately {
		pos.TriggerExit(blockTime, tier.ExitDuration)
	}

	return pos, nil
}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L142-215)
```go
func (k Keeper) processEventsAndClaimBonus(ctx context.Context, pos *types.PositionState) (sdk.Coins, error) {
	// Rewards should have been claimed before undelegation
	if !pos.IsDelegated() {
		return sdk.NewCoins(), nil
	}

	valAddr, err := sdk.ValAddressFromBech32(pos.Delegation.ValidatorAddress)
	if err != nil {
		return nil, err
	}

	events, err := k.getValidatorEventsSince(ctx, valAddr, pos.LastEventSeq)
	if err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	blockTime := sdkCtx.BlockTime()

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

	applyBonusAccrualCheckpoint(&pos.Position, blockTime)
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L237-241)
```go
	}

	if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.RewardsPoolName, ownerAddr, bonusCoins); err != nil {
		return nil, err
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
