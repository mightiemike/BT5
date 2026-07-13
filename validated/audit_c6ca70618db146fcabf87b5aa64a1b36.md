### Title
`LastKnownBonded` Hardcoded to `true` on Redelegate Allows Bonus Accrual During Validator Unbonding Period — (`File: x/tieredrewards/keeper/msg_server.go`)

---

### Summary

In `TierRedelegate`, after moving a position to a destination validator, the module unconditionally sets `LastKnownBonded = true` regardless of whether the destination validator is actually bonded. If the destination validator is in the **unbonding** state at the time of redelegate, the position's accrual state is corrupted: the next `processEventsAndClaimBonus` call will compute bonus rewards for the entire unbonding gap (from redelegate time to the validator's eventual re-bond), draining the `RewardsPool` for a period during which no bonus should have accrued.

---

### Finding Description

In `TierRedelegate` (`msg_server.go`), after claiming rewards and executing the staking redelegate, the module resets the position's bonus checkpoints:

```go
latestSeq, err := ms.getValidatorEventLatestSeq(ctx, dstValAddr)
...
pos.UpdateBonusCheckpoints(latestSeq, sdkCtx.BlockTime(), true)  // ← hardcoded true
``` [1](#0-0) 

The third argument (`true`) unconditionally sets `LastKnownBonded = true` for the position. This is the value that `processEventsAndClaimBonus` reads as `bonded` at the start of its segment loop:

```go
bonded := pos.LastKnownBonded
segmentStart := pos.LastBonusAccrual
...
for _, entry := range events {
    if bonded {
        bonus := k.computeSegmentBonus(*pos, tier, segmentStart, evt.Timestamp, evt.TokensPerShare)
        totalBonus = totalBonus.Add(bonus)
    }
    switch evt.EventType {
    case types.ValidatorEventType_VALIDATOR_EVENT_TYPE_UNBOND:
        bonded = false
    case types.ValidatorEventType_VALIDATOR_EVENT_TYPE_BOND:
        bonded = true
    ...
    }
    segmentStart = evt.Timestamp
``` [2](#0-1) 

The Cosmos SDK `BeginRedelegation` only blocks redelegation to **unbonded** validators; redelegation to an **unbonding** validator is permitted. When the destination validator is unbonding at redelegate time, its UNBOND event was already recorded at some sequence `N`. `getValidatorEventLatestSeq` returns `N`, so `LastEventSeq = N`. The UNBOND event itself is excluded from future processing (range is `StartExclusive(N)`). The next event the position will see is the validator's eventual BOND event at seq `N+1`.

When the user later calls `ClaimTierRewards`, `processEventsAndClaimBonus` starts with `bonded = true` (from the corrupted `LastKnownBonded`) and `segmentStart = redelegate_block_time`. It processes the BOND event at seq `N+1` and, because `bonded = true`, computes bonus for the entire interval `[redelegate_time, bond_event_time]` — the full unbonding gap — using `evt.TokensPerShare` from the BOND event:

```go
bonus := k.computeSegmentBonus(*pos, tier, segmentStart, evt.Timestamp, evt.TokensPerShare)
totalBonus = totalBonus.Add(bonus)
``` [3](#0-2) 

The defensive check at the end of `processEventsAndClaimBonus` only guards the **final** open segment:

```go
if bonded && val.IsBonded() {
    ...
    bonus := k.computeSegmentBonus(*pos, tier, segmentStart, blockTime, currentRate)
``` [4](#0-3) 

It does not retroactively correct the already-computed intermediate segment bonus for the unbonding gap.

---

### Impact Explanation

The `RewardsPool` module account is debited for bonus rewards that were never legitimately earned:

```go
if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.RewardsPoolName, ownerAddr, bonusCoins); err != nil {
``` [5](#0-4) 

The attacker receives `Amount × BonusApy × (unbonding_duration / SecondsPerYear)` in bonus tokens they did not earn. For a 21-day unbonding period and a 10% APY tier, this is approximately 0.575% of the position's value per exploit cycle. The `RewardsPool` is drained by this amount, reducing the pool available to all other legitimate position holders.

---

### Likelihood Explanation

Any position owner can trigger this by sending a standard `MsgTierRedelegate` transaction targeting a validator that is currently in the unbonding state. Validators enter the unbonding state routinely (jailing, voluntary unbonding). No privileged access, leaked keys, or social engineering is required. The attacker only needs to monitor validator state and time the redelegate accordingly.

---

### Recommendation

In `TierRedelegate`, replace the hardcoded `true` with a live check of the destination validator's bonded status before calling `UpdateBonusCheckpoints`:

```go
dstVal, err := ms.stakingKeeper.GetValidator(ctx, dstValAddr)
if err != nil {
    return nil, err
}
pos.UpdateBonusCheckpoints(latestSeq, sdkCtx.BlockTime(), dstVal.IsBonded())
```

This mirrors the defensive check already present at the end of `processEventsAndClaimBonus` and ensures `LastKnownBonded` accurately reflects the destination validator's state at the moment of redelegate.

---

### Proof of Concept

1. Alice holds a tier position delegated to validator V1 (bonded).
2. Validator V2 begins unbonding; its UNBOND event is recorded at seq=5.
3. Alice sends `MsgTierRedelegate` with `DstValidator = V2`.
   - `claimRewards` runs cleanly; `LastBonusAccrual = T_redelegate`.
   - `getValidatorEventLatestSeq(V2)` returns 5.
   - `UpdateBonusCheckpoints(5, T_redelegate, true)` → `LastKnownBonded = true`, `LastEventSeq = 5`.
4. 21 days later, V2 re-bonds; BOND event recorded at seq=6 with timestamp `T_bond`.
5. Alice sends `MsgClaimTierRewards`.
   - `processEventsAndClaimBonus` starts: `bonded = true`, `segmentStart = T_redelegate`.
   - Processes seq=6 (BOND event): `bonded = true` → computes bonus for `[T_redelegate, T_bond]` (21 days of unbonding period).
   - `SendCoinsFromModuleToAccount(RewardsPool, Alice, bonus)` executes.
6. Alice receives bonus rewards for 21 days during which V2 was unbonded and no bonus should have accrued.

### Citations

**File:** x/tieredrewards/keeper/msg_server.go (L257-263)
```go
	latestSeq, err := ms.getValidatorEventLatestSeq(ctx, dstValAddr)
	if err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	pos.UpdateBonusCheckpoints(latestSeq, sdkCtx.BlockTime(), true)
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L163-199)
```go
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
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L206-212)
```go
	if bonded && val.IsBonded() {
		currentRate, err := k.getTokensPerShare(ctx, valAddr)
		if err != nil {
			return nil, err
		}
		bonus := k.computeSegmentBonus(*pos, tier, segmentStart, blockTime, currentRate)
		totalBonus = totalBonus.Add(bonus)
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L239-239)
```go
	if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.RewardsPoolName, ownerAddr, bonusCoins); err != nil {
```
