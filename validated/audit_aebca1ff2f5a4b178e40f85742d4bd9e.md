### Title
`MsgCommitDelegationToTier` Initializes `LastKnownBonded = true` Without Verifying Validator Is Bonded, Enabling Overbonding of Bonus Rewards - (`x/tieredrewards/keeper/msg_server.go`, `x/tieredrewards/keeper/position.go`)

---

### Summary

`MsgCommitDelegationToTier` does not verify that the target validator is bonded before creating a position. The shared `createDelegatedPosition` helper hardcodes `LastKnownBonded = true` under the design assumption that "only bonded validators are accepted." `MsgLockTier` enforces this assumption with an explicit validator-bonded check before calling `createDelegatedPosition`. `MsgCommitDelegationToTier` does not. As a result, a position can be created on an unbonding validator with `LastKnownBonded = true`, and the first subsequent `BOND` event causes `processEventsAndClaimBonus` to compute bonus for the entire period `[creation_time, BOND_event_time]` as if the validator was bonded throughout — yielding unauthorized bonus rewards from the rewards pool.

---

### Finding Description

`createDelegatedPosition` always passes `true` as `lastKnownBonded` to `NewPosition`:

```go
// x/tieredrewards/keeper/position.go
pos := types.NewPosition(id, owner, tier.Id, delAddr.String(), blockHeight, lastEventSeq, blockTime, true, blockTime)
```

The inline comment documents the assumption: *"True when the position is first created (only bonded validators accepted)."* [1](#0-0) 

`MsgLockTier` enforces this assumption — the ADR explicitly lists "validator bonded" as a key validation for `MsgLockTier`. [2](#0-1) 

`MsgCommitDelegationToTier` does not list "validator bonded" as a key validation. Its only validations are: `amount <= user's delegation`, `amount >= MinLockAmount`, `tier not close-only`, and `delegator is not a vesting account`. [3](#0-2) 

Critically, the `validateNewPosition` call inside `CommitDelegationToTier` takes `(ctx, delegatorAddress, amount, tier)` — it has no `validatorAddress` parameter and therefore structurally cannot check validator bonded status: [4](#0-3) 

`processEventsAndClaimBonus` seeds the bonus replay loop with `bonded := pos.LastKnownBonded` and `segmentStart := pos.LastBonusAccrual` (creation time). For each event, if `bonded == true`, it computes bonus for `[segmentStart, event.Timestamp]` using the event's `TokensPerShare` snapshot: [5](#0-4) 

If the validator was unbonding at position creation and later becomes bonded (`BOND` event recorded), the loop enters with `bonded = true`, hits the `BOND` event, and computes bonus for `[creation_time, BOND_event_time]` — a period during which the validator was **not** bonded. The `BOND` event then sets `bonded = true` (no change), and the final segment `[BOND_event_time, blockTime]` is also paid. The user receives bonus for the entire period from creation, not just from when the validator re-bonded. [6](#0-5) 

---

### Impact Explanation

The corrupted value is the `tieredrewards` rewards pool balance (`types.RewardsPoolName` module account). The attacker receives bonus tokens they are not entitled to — specifically, bonus for the unbonded gap `[creation_time, BOND_event_time]`. The rewards pool is debited by `SendCoinsFromModule(rewards_pool, owner, bonus)`. This is a direct, quantifiable fund loss from the module account. [7](#0-6) 

---

### Likelihood Explanation

The attack requires:
1. A validator to transition bonded → unbonding (jailing for downtime or double-sign, or power drop).
2. The attacker to hold a delegation to that validator (common — any delegator qualifies).
3. The attacker to call `MsgCommitDelegationToTier` during the unbonding window (permissionless, no special role needed).
4. The validator to re-bond (unjailing or power recovery).

All four steps are routine on a live chain. Validators are jailed and unjailed regularly. The attacker pays only gas. The window between jailing and unjailing can be hours to days, giving ample time to submit the transaction. [8](#0-7) 

---

### Recommendation

Add an explicit validator-bonded check in `CommitDelegationToTier` before calling `createDelegatedPosition`, mirroring the check already present in `LockTier`. Alternatively, derive `lastKnownBonded` from the live validator status inside `createDelegatedPosition` rather than hardcoding `true`, so the assumption is enforced at the point of use regardless of which message path calls it. [9](#0-8) 

---

### Proof of Concept

1. Validator `V` is bonded. Attacker delegates 1 000 000 `basecro` to `V`.
2. `V` is jailed (downtime) → status becomes `Unbonding`. `AfterValidatorBeginUnbonding` fires; `UNBOND` event recorded with `ReferenceCount = N` (existing positions on `V`).
3. Attacker calls `MsgCommitDelegationToTier` specifying `V`. No validator-bonded check fires. `transferDelegationToPosition` succeeds (Cosmos SDK allows delegation to an unbonding validator). `createDelegatedPosition` sets `LastEventSeq = latestSeq` (skipping the `UNBOND` event) and `LastKnownBonded = true`. `setPosition` stores the position.
4. `V` is unjailed → status becomes `Bonded`. `AfterValidatorBonded` fires; `BOND` event recorded at seq `S+1` with `ReferenceCount = N+1` (now includes attacker's position).
5. Attacker calls `MsgClaimTierRewards`. `processEventsAndClaimBonus` runs:
   - `bonded = true`, `segmentStart = creation_time`
   - Processes `BOND` event at seq `S+1`: `bonded == true` → computes bonus for `[creation_time, BOND_event_time]` (validator was unbonding during this entire window).
   - Final segment `[BOND_event_time, blockTime]`: validator is now bonded → bonus computed correctly.
6. Attacker receives bonus for `[creation_time, BOND_event_time]` — the full unbonded gap — draining the rewards pool by an amount proportional to `shares × tokensPerShare × bonusApy × unbondedDuration / SecondsPerYear`. [10](#0-9)

### Citations

**File:** x/tieredrewards/keeper/position.go (L34-71)
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
```

**File:** doc/architecture/adr-006.md (L157-157)
```markdown
| **MsgLockTier** | Lock tokens + delegate to validator. Optional `trigger_exit_immediately`. | amount >= MinLockAmount; validator bonded; tier not close-only; **owner is not a vesting account** |
```

**File:** doc/architecture/adr-006.md (L158-158)
```markdown
| **MsgCommitDelegationToTier** | Transfer existing delegation to tier (no unbonding). Partial allowed. | amount <= user's delegation; amount >= MinLockAmount; tier not close-only; **delegator is not a vesting account** |
```

**File:** x/tieredrewards/keeper/msg_server.go (L89-149)
```go
func (ms msgServer) CommitDelegationToTier(ctx context.Context, msg *types.MsgCommitDelegationToTier) (*types.MsgCommitDelegationToTierResponse, error) {
	if err := msg.Validate(); err != nil {
		return nil, err
	}

	tier, err := ms.getTier(ctx, msg.Id)
	if err != nil {
		return nil, err
	}

	if err := ms.validateNewPosition(ctx, msg.DelegatorAddress, msg.Amount, tier); err != nil {
		return nil, err
	}

	valAddr, err := sdk.ValAddressFromBech32(msg.ValidatorAddress)
	if err != nil {
		return nil, err
	}

	delegatorAddr, err := sdk.AccAddressFromBech32(msg.DelegatorAddress)
	if err != nil {
		return nil, err
	}

	id, err := ms.NextPositionId.Peek(ctx)
	if err != nil {
		return nil, err
	}

	delAddr, err := ms.createPositionDelegatorAccount(ctx, delegatorAddr, id)
	if err != nil {
		return nil, err
	}

	if _, err := ms.transferDelegationToPosition(ctx, msg.DelegatorAddress, delAddr, msg.ValidatorAddress, msg.Amount); err != nil {
		return nil, err
	}

	pos, err := ms.createDelegatedPosition(ctx, msg.DelegatorAddress, tier, valAddr, delAddr, msg.TriggerExitImmediately)
	if err != nil {
		return nil, err
	}

	// Defensive, but should not happen since transactions are sequential
	if pos.Id != id {
		return nil, errorsmod.Wrapf(types.ErrInvalidPositionID, "position id mismatch: peeked %d, created %d", id, pos.Id)
	}

	if err := ms.setPosition(ctx, pos, &ValidatorTransition{PreviousAddress: ""}); err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)

	if err := sdkCtx.EventManager().EmitTypedEvent(&types.EventDelegationCommitted{
		Position: pos,
	}); err != nil {
		return nil, err
	}

	return &types.MsgCommitDelegationToTierResponse{PositionId: pos.Id}, nil
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L161-179)
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
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L183-213)
```go
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

**File:** x/tieredrewards/keeper/claim_rewards.go (L219-229)
```go
	if totalBonus.IsZero() {
		return sdk.NewCoins(), nil
	}

	bondDenom, err := k.stakingKeeper.BondDenom(ctx)
	if err != nil {
		return nil, err
	}

	bonusCoins := sdk.NewCoins(sdk.NewCoin(bondDenom, totalBonus))

```
