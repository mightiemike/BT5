### Title
Orphaned Validator Events After Removal Permanently Block Bonus Reward Claims and Leak Storage — (`x/tieredrewards/keeper/hooks.go`)

### Summary

When a validator is removed while positions still hold unprocessed validator events, `AfterValidatorRemoved` intentionally skips `ValidatorEventSeq` cleanup. However, because the validator's delegations are already cleared by the time removal fires, all affected positions have `IsDelegated() == false`, causing `processEventsAndClaimBonus` to return early without ever decrementing the orphaned event reference counts. Those events and their sequence counter remain in storage permanently, and the accrued bonus rewards for the pre-removal bonded segment are irretrievably locked in the `RewardsPoolName` module account.

---

### Finding Description

**Step 1 — UNBOND event is appended with a non-zero reference count.**

When a validator begins unbonding (e.g., due to jailing), `AfterValidatorBeginUnbonding` fires and appends a `VALIDATOR_EVENT_TYPE_UNBOND` event whose `ReferenceCount` equals the current position count for that validator. [1](#0-0) 

**Step 2 — Positions do not claim during the unbonding window.**

During the unbonding period the delegation still exists, so `IsDelegated()` would return `true` and `processEventsAndClaimBonus` would process the event and decrement its reference count. If no position calls `ClaimTierRewards` (or any other reward-claiming path) before the unbonding completes, the event's `ReferenceCount` remains > 0.

**Step 3 — Unbonding completes; delegations are deleted from staking state.**

`getDelegation` queries `GetDelegatorDelegations` with limit 1. Once the unbonding period elapses the staking module removes the delegation record, so `getDelegation` returns `nil`. [2](#0-1) 

**Step 4 — `AfterValidatorRemoved` detects leftover events and skips cleanup.**

`hasValidatorEvents` finds the still-live UNBOND event and returns `true`. The hook logs an error and returns `nil` without calling `deleteValidatorEventSeq`. The comment says the seq is "still needed for future claims," but that assumption is broken because the delegation is already gone. [3](#0-2) 

**Step 5 — Future `ClaimTierRewards` calls silently skip bonus processing.**

`processEventsAndClaimBonus` guards on `pos.IsDelegated()` at the very top. With the delegation gone, it returns `sdk.NewCoins()` immediately — no events are walked, no `decrementEventRefCount` is called. [4](#0-3) 

**Step 6 — Events and seq counter are permanently orphaned.**

`decrementEventRefCount` is never reached, so the event's `ReferenceCount` never reaches zero and `ValidatorEvents.Remove` is never called. `ValidatorEventSeq` was also skipped in step 4. Both KV entries persist indefinitely. [5](#0-4) 

---

### Impact Explanation

1. **Permanent storage leak.** Every `ValidatorEvent` entry (keyed by `(valAddr, seq)`) and the `ValidatorEventSeq` entry for the removed validator remain in the KV store forever. Over time, across multiple validator removals, this accumulates unbounded orphaned state.

2. **Irretrievable bonus reward loss.** The bonus rewards that correspond to the bonded segment between the position's `LastBonusAccrual` and the UNBOND event timestamp are never paid out. The funds sit in `RewardsPoolName` but are permanently inaccessible to the affected positions, because `processEventsAndClaimBonus` will always short-circuit on `!pos.IsDelegated()` for those positions going forward. [6](#0-5) 

---

### Likelihood Explanation

Validator jailing followed by removal is a normal, non-privileged chain event (downtime slashing, double-sign). Position holders are not required to claim rewards on any schedule. The combination — validator removed while at least one position has an unprocessed event — is therefore a realistic steady-state condition on a live chain, not a contrived edge case.

---

### Recommendation

`AfterValidatorRemoved` should not silently skip cleanup when events exist. Instead, it should iterate all remaining events for the validator and forcibly zero out their reference counts (deleting each event), then delete `ValidatorEventSeq`. Any accrued but unclaimed bonus for those events should either be paid out at removal time (by iterating affected positions) or explicitly written off with an on-chain event so the accounting is transparent. The current "log and skip" approach leaves the system in an inconsistent state with no recovery path.

---

### Proof of Concept

```
1. Create a tier and two positions delegated to validator V.
2. Advance one block so AfterValidatorBonded has fired (or skip if already bonded).
3. Jail validator V → AfterValidatorBeginUnbonding fires →
   ValidatorEvents[(V, 1)] = {UNBOND, ReferenceCount: 2}.
4. Do NOT call ClaimTierRewards for either position.
5. Advance blocks past the unbonding period → staking removes delegations.
6. Trigger validator removal → AfterValidatorRemoved fires →
   hasValidatorEvents returns true → logs error, returns nil.
7. Assert: ValidatorEvents[(V, 1)] still exists with ReferenceCount == 2.
8. Assert: ValidatorEventSeq[V] still exists.
9. Call ClaimTierRewards for both positions →
   processEventsAndClaimBonus returns ([], nil) immediately (IsDelegated == false).
10. Assert: ValidatorEvents[(V, 1)] STILL exists (ref count never decremented).
11. Assert: bonus rewards = 0, even though a bonded segment existed before the UNBOND event.
``` [7](#0-6) [8](#0-7)

### Citations

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

**File:** x/tieredrewards/keeper/hooks.go (L81-95)
```go
func (h Hooks) AfterValidatorRemoved(ctx context.Context, _ sdk.ConsAddress, valAddr sdk.ValAddress) error {
	has, err := h.k.hasValidatorEvents(ctx, valAddr)
	if err != nil {
		return err
	}
	if has {
		h.k.logger(ctx).Error("leftover validator events found on validator removal, skipping cleanup", "validator", valAddr.String())
		return nil
	}

	if err := h.k.deleteValidatorEventSeq(ctx, valAddr); err != nil {
		h.k.logger(ctx).Error("failed to cleanup validator event sequence on validator removal", "validator", valAddr.String(), "error", err)
	}
	return nil
}
```

**File:** x/tieredrewards/keeper/delegation.go (L72-87)
```go
func (k Keeper) getDelegation(ctx context.Context, delegatorAddress string) (*stakingtypes.Delegation, error) {
	delAddr, err := sdk.AccAddressFromBech32(delegatorAddress)
	if err != nil {
		return nil, errorsmod.Wrap(sdkerrors.ErrInvalidAddress, "invalid delegator address")
	}
	// A position has at most one delegation.
	dels, err := k.stakingKeeper.GetDelegatorDelegations(ctx, delAddr, 1)
	if err != nil {
		return nil, err
	}
	if len(dels) == 0 {
		return nil, nil
	}
	d := dels[0]
	return &d, nil
}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L142-146)
```go
func (k Keeper) processEventsAndClaimBonus(ctx context.Context, pos *types.PositionState) (sdk.Coins, error) {
	// Rewards should have been claimed before undelegation
	if !pos.IsDelegated() {
		return sdk.NewCoins(), nil
	}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L196-198)
```go
		if err := k.decrementEventRefCount(ctx, valAddr, entry.Seq); err != nil {
			return nil, err
		}
```

**File:** x/tieredrewards/keeper/validator_events.go (L83-101)
```go
// decrementEventRefCount decrements the reference count of a validator event.
// If the reference count reaches zero, the event is deleted.
func (k Keeper) decrementEventRefCount(ctx context.Context, valAddr sdk.ValAddress, seq uint64) error {
	key := collections.Join(valAddr, seq)
	event, err := k.ValidatorEvents.Get(ctx, key)
	if errors.Is(err, collections.ErrNotFound) {
		return nil // already cleaned up
	}
	if err != nil {
		return err
	}

	if event.ReferenceCount <= 1 {
		return k.ValidatorEvents.Remove(ctx, key)
	}

	event.ReferenceCount--
	return k.ValidatorEvents.Set(ctx, key, event)
}
```

**File:** x/tieredrewards/keeper/validator_events.go (L116-124)
```go
func (k Keeper) hasValidatorEvents(ctx context.Context, valAddr sdk.ValAddress) (bool, error) {
	rng := collections.NewPrefixedPairRange[sdk.ValAddress, uint64](valAddr)
	found := false
	err := k.ValidatorEvents.Walk(ctx, rng, func(_ collections.Pair[sdk.ValAddress, uint64], _ types.ValidatorEvent) (bool, error) {
		found = true
		return true, nil
	})
	return found, err
}
```
