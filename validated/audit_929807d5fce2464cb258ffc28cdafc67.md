### Title
Hardcoded `LastKnownBonded=true` at Position Creation and Redelegate Allows Bonus Accrual During Unbonded Validator Segments — (`x/tieredrewards/keeper/position.go`, `x/tieredrewards/keeper/msg_server.go`)

---

### Summary

`LastKnownBonded` is hardcoded to `true` in two production paths: position creation (`createDelegatedPosition`) and redelegation (`TierRedelegate`). When either path targets a currently-unbonded validator, the stale `true` checkpoint causes `processEventsAndClaimBonus` to compute bonus for the unbonded gap up to the validator's next BOND event, draining `RewardsPoolName` for time the validator was not bonded.

---

### Finding Description

**Root cause 1 — position creation:** [1](#0-0) 

```go
pos := types.NewPosition(id, owner, tier.Id, delAddr.String(), blockHeight, lastEventSeq, blockTime, true, blockTime)
```

The eighth argument (`lastKnownBonded`) is unconditionally `true`, regardless of the validator's actual bonded state at creation time.

**Root cause 2 — redelegation:** [2](#0-1) 

```go
pos.UpdateBonusCheckpoints(latestSeq, sdkCtx.BlockTime(), true)
```

After redelegating to a destination validator, `LastKnownBonded` is again hardcoded to `true`, regardless of whether the destination validator is currently bonded.

**How `processEventsAndClaimBonus` uses this value:** [3](#0-2) 

```go
bonded := pos.LastKnownBonded
segmentStart := pos.LastBonusAccrual
```

The loop then computes bonus for every segment where `bonded == true`: [4](#0-3) 

```go
if bonded {
    bonus := k.computeSegmentBonus(*pos, tier, segmentStart, evt.Timestamp, evt.TokensPerShare)
    totalBonus = totalBonus.Add(bonus)
}
```

The defensive check at line 206 only guards the **current trailing segment** (after all events are replayed): [5](#0-4) 

```go
if bonded && val.IsBonded() {
    ...
    bonus := k.computeSegmentBonus(...)
    totalBonus = totalBonus.Add(bonus)
}
```

It does **not** retroactively protect historical segments computed inside the event loop. If `LastKnownBonded=true` is stale and the first pending event is a BOND event, the segment `[LastBonusAccrual, BOND_event.Timestamp]` is computed as bonded even though the validator was unbonded for that entire interval.

---

### Impact Explanation

Bonus coins are sent from `RewardsPoolName` to the position owner via: [6](#0-5) 

```go
k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.RewardsPoolName, ownerAddr, bonusCoins)
```

The pool is drained by an amount proportional to `shares × tokensPerShare × BonusAPY × unbonded_duration / SecondsPerYear`. This is unbacked value leaving the module boundary for a period when no bonus should have accrued.

---

### Likelihood Explanation

The redelegate path is the most accessible exploit vector. Cosmos SDK permits redelegating to an unbonded (jailed) validator. An attacker with an existing position can:

1. Redelegate to a jailed validator via `MsgTierRedelegate` — `UpdateBonusCheckpoints(..., true)` is called, setting `LastKnownBonded=true` against an unbonded validator.
2. Wait for the validator to unjail and re-bond — the staking module fires `AfterValidatorBonded`, recording a BOND event.
3. Call `MsgClaimTierRewards` — `processEventsAndClaimBonus` computes bonus for `[redelegate_time, bond_time]`, the entire unbonded gap.

The position-creation path (`LockTier` / `CommitDelegationToTier`) is also affected if the target validator is unbonded at creation time, subject to any validator-status guard in `validateNewPosition` (not examined here, but the hardcoding is unconditional regardless).

---

### Recommendation

Replace the hardcoded `true` with a live validator bond-status check at both sites:

1. In `createDelegatedPosition`, query `stakingKeeper.GetValidator` and pass `val.IsBonded()` as `lastKnownBonded`.
2. In `TierRedelegate`, do the same for the destination validator before calling `UpdateBonusCheckpoints`.

---

### Proof of Concept

```
Block 1:  Validator V jailed → AfterValidatorBeginUnbonding fires → UNBOND event seq=1
Block 2:  Attacker calls MsgTierRedelegate (existing position) to V
          → claimRewards settles old validator correctly
          → UpdateBonusCheckpoints(seq=1, T2, true)   ← LastKnownBonded=true, V is unbonded
Block 3:  V unjailed → AfterValidatorBonded fires → BOND event seq=2 at time T3
Block 4:  Attacker calls MsgClaimTierRewards
          processEventsAndClaimBonus:
            bonded = true  (stale)
            segmentStart = T2
            event[seq=2]: BOND at T3
              bonded==true → computeSegmentBonus(T2, T3, rate)  ← INCORRECT bonus paid
              bonded = true
          After loop: bonded && val.IsBonded() → bonus for [T3, T4] also paid (correct)
          Total: bonus for [T2, T4] paid, but [T2, T3] was unbonded → pool drained incorrectly
```

Assert: bonus received for `[T2, T3]` must be zero; it is not.

#Vulnerability found.

### Citations

**File:** x/tieredrewards/keeper/position.go (L56-56)
```go
	pos := types.NewPosition(id, owner, tier.Id, delAddr.String(), blockHeight, lastEventSeq, blockTime, true, blockTime)
```

**File:** x/tieredrewards/keeper/msg_server.go (L263-263)
```go
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

**File:** x/tieredrewards/keeper/claim_rewards.go (L206-213)
```go
	if bonded && val.IsBonded() {
		currentRate, err := k.getTokensPerShare(ctx, valAddr)
		if err != nil {
			return nil, err
		}
		bonus := k.computeSegmentBonus(*pos, tier, segmentStart, blockTime, currentRate)
		totalBonus = totalBonus.Add(bonus)
	}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L239-239)
```go
	if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.RewardsPoolName, ownerAddr, bonusCoins); err != nil {
```
