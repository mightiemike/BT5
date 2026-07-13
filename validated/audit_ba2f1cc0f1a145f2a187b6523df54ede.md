### Title
Stale `LastKnownBonded` Causes Bonus Reward Denial for Positions on Bonded Validators — (`x/tieredrewards/keeper/claim_rewards.go`)

### Summary

In `processEventsAndClaimBonus`, the final-segment bonus check gates on `bonded && val.IsBonded()`, where `bonded` is derived from the stored `pos.LastKnownBonded` plus event replay. When `bonded` is `false` (stale stored state) but `val.IsBonded()` is `true` (live state), the condition evaluates to `false` and the position accrues **zero bonus for the current bonded segment**, even though the validator is actively bonded. This is the direct analog of the external report: a stored value that should be computed in real-time from current chain state is instead read from storage and only updated when specific transactions occur.

### Finding Description

`processEventsAndClaimBonus` in `claim_rewards.go` initialises the bonded-state variable from the persisted field:

```go
bonded := pos.LastKnownBonded   // line 164 — stored value
```

After replaying all pending validator events it reaches the final-segment check:

```go
if bonded && val.IsBonded() {   // line 206
    currentRate, err := k.getTokensPerShare(ctx, valAddr)
    bonus := k.computeSegmentBonus(*pos, tier, segmentStart, blockTime, currentRate)
    totalBonus = totalBonus.Add(bonus)
}
```

Then it persists the **replayed** state — not the live state — back to storage:

```go
pos.UpdateLastKnownBonded(bonded)   // line 217 — saves replayed state, not val.IsBonded()
```

The staking hooks that record `BOND` / `UNBOND` events skip recording when `count == 0`:

```go
// hooks.go line 58-60
if count == 0 {
    return nil
}
```

This creates the following divergence path:

1. All positions on validator V are cleared → `count = 0`.
2. V unbonds → no `UNBOND` event recorded (count was 0).
3. V rebonds → no `BOND` event recorded (count was 0).
4. A new position P is created on V. `LastKnownBonded` is the Go zero value `false` (or whatever the unset default is at creation time).
5. No events exist since P was created, so event replay leaves `bonded = false`.
6. Live state: `val.IsBonded() = true`.
7. Final-segment check: `false && true = false` → **no bonus computed**.
8. `pos.UpdateLastKnownBonded(false)` persists the stale `false` again.
9. Every subsequent `ClaimTierRewards` call repeats steps 5–8 indefinitely.

The position never accrues bonus until V undergoes a full unbond→rebond cycle **while count > 0**, which records a `BOND` event that finally sets `bonded = true`.

### Impact Explanation

The corrupted value is the **bonus reward balance** of the position owner. Bonus is computed as:

```
shares × tokensPerShare × tier.BonusApy × durationSeconds / SecondsPerYear
```

For a position that should accrue bonus continuously but has `LastKnownBonded = false`, every call to `ClaimTierRewards`, `AddToPosition`, `Undelegate`, `Redelegate`, or `ClearPosition` (all of which call `processEventsAndClaimBonus`) returns zero bonus. The owner permanently loses the bonus entitlement for the entire bonded period until a corrective event is recorded. This is a direct, quantifiable loss of user funds from the `RewardsPoolName` module account that the user is entitled to receive.

### Likelihood Explanation

The trigger is reachable by any unprivileged user via `MsgLockTier` or `MsgCommitDelegationToTier` on a validator that was previously fully vacated and has since rebonded. Validator churn (jailing, tombstoning, forced exit of all positions) is a normal production event on a live PoS chain. The scenario requires no privileged access, no leaked keys, and no social engineering — only the standard position-creation message path.

### Recommendation

For the **final segment** (from the last recorded event to the current block), only the live validator state is relevant. Replace:

```go
// claim_rewards.go line 206
if bonded && val.IsBonded() {
```

with:

```go
if val.IsBonded() {
```

The `bonded` variable correctly gates **historical** inter-event segments (lines 175–179). It must not gate the final segment, because the final segment's eligibility is determined solely by the current live bonded state, not by the stale replayed state.

Additionally, initialise `LastKnownBonded` to the current validator bonded state at position creation time (in `MsgLockTier` / `MsgCommitDelegationToTier`) so that newly created positions on already-bonded validators start with `LastKnownBonded = true`.

### Proof of Concept

1. Deploy chain with validator V and no tier positions.
2. Jail V → V begins unbonding. No `UNBOND` event is recorded (count = 0). [1](#0-0) 
3. Unjail V → V rebonds. No `BOND` event is recorded (count = 0). [2](#0-1) 
4. User sends `MsgLockTier` creating position P on V. `LastKnownBonded` defaults to `false`.
5. Advance 30 days. Call `MsgClaimTierRewards` for P.
6. Inside `processEventsAndClaimBonus`: `bonded = false` (no

### Citations

**File:** x/tieredrewards/keeper/hooks.go (L27-35)
```go
func (h Hooks) AfterValidatorBeginUnbonding(ctx context.Context, _ sdk.ConsAddress, valAddr sdk.ValAddress) error {
	count, err := h.k.getPositionCountForValidator(ctx, valAddr)
	if err != nil {
		return err
	}
	if count == 0 {
		return nil
	}

```

**File:** x/tieredrewards/keeper/hooks.go (L53-60)
```go
func (h Hooks) AfterValidatorBonded(ctx context.Context, _ sdk.ConsAddress, valAddr sdk.ValAddress) error {
	count, err := h.k.getPositionCountForValidator(ctx, valAddr)
	if err != nil {
		return err
	}
	if count == 0 {
		return nil
	}
```
