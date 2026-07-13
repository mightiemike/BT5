### Title
`TierRedelegate` hardcodes `LastKnownBonded = true` without verifying destination validator bonded state, disconnecting tier position from staking module — (`x/tieredrewards/keeper/msg_server.go`)

---

### Summary

`MsgTierRedelegate` sets `LastKnownBonded = true` unconditionally after redelegating a tier position to a new validator, without checking whether that destination validator is actually bonded. Because `processEventsAndClaimBonus` uses `LastKnownBonded` as the starting bonded state for lazy bonus replay, a position redelegated to an unbonding validator will incorrectly accrue bonus rewards for the entire unbonded gap once the validator re-bonds.

---

### Finding Description

In `TierRedelegate`, after the staking redelegation executes, the position's bonus checkpoints are reset:

```go
latestSeq, err := ms.getValidatorEventLatestSeq(ctx, dstValAddr)
...
pos.UpdateBonusCheckpoints(latestSeq, sdkCtx.BlockTime(), true)  // true = LastKnownBonded
``` [1](#0-0) 

`latestSeq` is set to the most recent event sequence for the destination validator. If that validator previously emitted an `UNBOND` event at seq N, then `latestSeq = N` and the position's `LastEventSeq = N`. The position will never replay the UNBOND event. Yet `LastKnownBonded` is hardcoded to `true`, asserting the validator was bonded after the last replay — which is false.

`processEventsAndClaimBonus` initialises its bonded-state variable directly from this persisted field:

```go
bonded := pos.LastKnownBonded
segmentStart := pos.LastBonusAccrual
``` [2](#0-1) 

When the destination validator later re-bonds (emitting a `BOND` event at seq N+1), the replay loop sees `bonded = true` and computes a full bonus segment from `LastBonusAccrual` (the redelegate block time) to the BOND event timestamp:

```go
if bonded {
    bonus := k.computeSegmentBonus(*pos, tier, segmentStart, evt.Timestamp, evt.TokensPerShare)
    totalBonus = totalBonus.Add(bonus)
}
``` [3](#0-2) 

The entire unbonded gap is treated as a bonded segment and paid out from the rewards pool.

The ADR confirms that `MsgTierRedelegate` does not require the destination validator to be bonded — its listed validations are only "Exit not elapsed; tier not close-only; Amount > 0; dst != src": [4](#0-3) 

The Cosmos SDK `BeginRedelegation` also does not require the destination validator to be bonded (only non-jailed), so the staking call

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

**File:** x/tieredrewards/keeper/claim_rewards.go (L164-165)
```go
	bonded := pos.LastKnownBonded
	segmentStart := pos.LastBonusAccrual
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L175-179)
```go
		if bonded {
			// Compute bonus for the bonded segment [segmentStart, eventTime]
			// using the snapshot rate at the event.
			bonus := k.computeSegmentBonus(*pos, tier, segmentStart, evt.Timestamp, evt.TokensPerShare)
			totalBonus = totalBonus.Add(bonus)
```

**File:** doc/architecture/adr-006.md (L160-161)
```markdown
| **MsgTierRedelegate** | Move delegation to another validator. Claims rewards first. | Exit not elapsed; tier not close-only; Amount > 0; dst != src |
| **MsgTierUndelegate** | Undelegate after exit commitment. Claims rewards first. Clears delegation state immediately. | Exit triggered; exit elapsed; delegated |
```
