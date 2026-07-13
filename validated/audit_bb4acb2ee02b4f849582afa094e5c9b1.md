Looking at the code carefully, I need to trace the exact path through `TierRedelegate` → `redelegate` → `BeginRedelegation` → `setRedelegationMapping` guard → `BeforeRedelegationSlashed` → `slashRedelegationPosition`.

**Key code locations:**

`TierRedelegate` in `msg_server.go` lines 245–255: [1](#0-0) 

`redelegate` in `delegation.go` lines 59–70 — checks only the **destination** validator's bonding status, not the source: [2](#0-1) 

`BeforeRedelegationSlashed` in `hooks.go` line 128–130: [3](#0-2) 

`slashRedelegationPosition` in `slash.go` lines 19–26 — returns nil on `ErrNotFound`: [4](#0-3) 

---

### Title
Bonus Rewards Lost on Slash After Instant Redelegate from Unbonded Validator — (`x/tieredrewards/keeper/msg_server.go`)

### Summary

`TierRedelegate` intentionally skips `setRedelegationMapping` when `BeginRedelegation` returns `unbondingID=0` (source validator is unbonded, redelegation is instant). However, the SDK still creates a `RedelegationEntry` with `UnbondingId=0` for the destination validator. When the destination validator is later slashed, the SDK's `SlashRedelegation` iterates those entries and fires `BeforeRedelegationSlashed(ctx, 0, sharesToUnbond)`. `slashRedelegationPosition` then calls `getRedelegationMapping(ctx, 0)`, finds no entry, and returns `nil` — skipping bonus settlement entirely. The position's shares are reduced by the slash without the pre-slash bonus ever being paid.

### Finding Description

**Step 1 — Source validator becomes unbonded after position creation.**
`delegate` (lines 48–49) enforces `IsBonded()` at creation time, so the position starts with a bonded validator. After creation, the validator can begin unbonding via normal chain operation.

**Step 2 — `TierRedelegate` is called; source is now unbonded.**
`redelegate` (lines 64–66) only checks `!val.IsBonded()` for the **destination** validator: [5](#0-4) 
No guard rejects a source validator that is unbonding/unbonded. `BeginRedelegation` returns `unbondingID=0` for instant redelegations.

**Step 3 — Mapping is skipped.**
The guard at lines 251–255 skips `setRedelegationMapping` when `unbondingID==0`: [6](#0-5) 
The comment's reasoning — *"no asynchronous completion hook will trigger"* — is correct for `AfterRedelegationCompleted` but **does not account for `BeforeRedelegationSlashed`**, which fires independently of the completion lifecycle.

**Step 4 — Destination validator is slashed.**
The SDK's `SlashRedelegation` iterates all `RedelegationEntry` records pointing to the slashed validator. For the instant-redelegate entry, `UnbondingId=0`. The SDK calls `BeforeRedelegationSlashed(ctx, 0, sharesToUnbond)`.

**Step 5 — `slashRedelegationPosition` silently no-ops.** [7](#0-6) 
`getRedelegationMapping(ctx, 0)` returns `collections.ErrNotFound`. The function returns `nil` without calling `processEventsAndClaimBonus`. The slash proceeds, reducing the position's shares. The bonus accrued up to the slash height is permanently lost.

### Impact Explanation

The position owner loses all bonus rewards accrued between the last claim and the slash event. The `processEventsAndClaimBonus` call that should settle pre-slash bonus (line 54 of `slash.go`) is never reached: [8](#0-7) 
After the slash, the position's validator event sequence advances past the pre-slash events, so those rewards can never be recovered on a future `ClaimTierRewards` call either.

### Likelihood Explanation

- Validators transitioning from bonded → unbonding is a routine chain event.
- A user redelegating away from an unbonding validator to a healthy one is a natural and expected action.
- The destination validator being slashed is a realistic (if infrequent) event.
- No privileged access or governance action is required by the attacker; the attacker is simply the position owner acting in their own interest.

### Recommendation

Remove the `unbondingID != 0` guard or handle the `unbondingID=0` case explicitly. Two options:

1. **Reject instant redelegations from unbonded validators** by adding a source-validator bonding check inside `redelegate` (mirroring the destination check), preventing `unbondingID=0` from ever occurring in `TierRedelegate`.

2. **Settle bonus before the redelegate** regardless of `unbondingID`. Since `claimRewards` is already called at line 229 before the redelegate, the bonus is settled at that point. The remaining gap is only for events that fire *between* `claimRewards` and the slash. The safer fix is option 1 — reject redelegation from unbonded source validators — since the position's delegation is already economically impaired and the user should undelegate instead.

### Proof of Concept

```
1. Create position delegated to validator A (bonded).
2. Begin unbonding validator A (AfterValidatorBeginUnbonding fires, UNBOND event recorded).
3. Call TierRedelegate(positionId, dstValidator=B) where B is bonded.
   → redelegate() passes (B is bonded), BeginRedelegation returns unbondingID=0.
   → setRedelegationMapping is skipped.
4. Advance blocks so bonus accrues on validator B.
5. Slash validator B (e.g., double-sign).
   → SDK calls SlashRedelegation → BeforeRedelegationSlashed(ctx, 0, shares).
   → slashRedelegationPosition: getRedelegationMapping(0) → ErrNotFound → return nil.
   → Bonus for the accrued period is NOT paid.
6. Call ClaimTierRewards(positionId).
   → Assert bonus received == 0 (or only post-slash amount).
   → Expected: bonus for the pre-slash accrual period should have been paid.
```

### Citations

**File:** x/tieredrewards/keeper/msg_server.go (L245-255)
```go
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
```

**File:** x/tieredrewards/keeper/delegation.go (L59-70)
```go
func (k Keeper) redelegate(ctx context.Context, delAddr sdk.AccAddress, srcValAddr, dstValAddr sdk.ValAddress, shares math.LegacyDec) (time.Time, uint64, error) {
	val, err := k.stakingKeeper.GetValidator(ctx, dstValAddr)
	if err != nil {
		return time.Time{}, 0, err
	}

	if !val.IsBonded() {
		return time.Time{}, 0, types.ErrValidatorNotBonded
	}

	return k.stakingKeeper.BeginRedelegation(ctx, delAddr, srcValAddr, dstValAddr, shares)
}
```

**File:** x/tieredrewards/keeper/hooks.go (L128-130)
```go
func (h Hooks) BeforeRedelegationSlashed(ctx context.Context, unbondingID uint64, sharesToUnbond sdkmath.LegacyDec) error {
	return h.k.slashRedelegationPosition(ctx, unbondingID, sharesToUnbond)
}
```

**File:** x/tieredrewards/keeper/slash.go (L19-26)
```go
func (k Keeper) slashRedelegationPosition(ctx context.Context, unbondingId uint64, sharesToUnbond math.LegacyDec) error {
	positionId, err := k.getRedelegationMapping(ctx, unbondingId)
	if errors.Is(err, collections.ErrNotFound) {
		return nil
	}
	if err != nil {
		return err
	}
```

**File:** x/tieredrewards/keeper/slash.go (L53-64)
```go
	// Settle bonus against PRE-slash shares.
	if _, err := k.processEventsAndClaimBonus(ctx, &pos); err != nil {
		// Deliberately forgo bonus rewards if pool is insufficient to prevent chain halt.
		if errors.Is(err, types.ErrInsufficientBonusPool) {
			k.logger(ctx).Error("insufficient bonus pool during redelegation slash",
				"position_id", pos.Id,
				"error", err.Error(),
			)
		} else {
			return err
		}
	}
```
