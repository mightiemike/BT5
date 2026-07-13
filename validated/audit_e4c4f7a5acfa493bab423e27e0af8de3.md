### Title
Missing Upper Bound on `ExitDuration` in `Tier.Validate()` Allows Governance to Permanently Lock User Staked Tokens - (File: `x/tieredrewards/types/tier.go`)

---

### Summary

`Tier.Validate()` enforces only a lower-bound check (`ExitDuration > 0`) on the tier's exit commitment duration, with no upper bound. A governance proposal that sets `ExitDuration` to an astronomically large value (e.g., `math.MaxInt64` nanoseconds ≈ 292 years) passes validation and is stored. Any position that subsequently calls `MsgTriggerExitFromTier` will have its `ExitUnlockAt` set centuries into the future, making `MsgTierUndelegate` and `MsgExitTierWithDelegation` permanently unreachable and locking the user's staked tokens inside the module indefinitely.

---

### Finding Description

`Tier.Validate()` in `x/tieredrewards/types/tier.go` performs the following check on `ExitDuration`:

```go
if t.ExitDuration <= 0 {
    return fmt.Errorf("exit duration must be positive")
}
```

There is no upper-bound guard. Any positive `time.Duration` value, including `math.MaxInt64` (≈ 292 years), passes validation and is persisted via `SetTier`.

`MsgTriggerExitFromTier` reads the live tier at call time and stamps `ExitUnlockAt` onto the position:

```go
tier, err := ms.getTier(ctx, pos.TierId)
...
pos.TriggerExit(sdkCtx.BlockTime(), tier.ExitDuration)
```

`TriggerExit` sets:

```go
p.ExitUnlockAt = blockTime.Add(duration)
```

Both exit paths — `MsgTierUndelegate` and `MsgExitTierWithDelegation` — gate on `CompletedExitLockDuration`, which requires `block_time >= ExitUnlockAt`. With `ExitUnlockAt` set centuries ahead, neither path is ever reachable, and the user's delegated tokens remain trapped in the module's per-position delegator account indefinitely.

`MsgUpdateTier` does not block updates to tiers that have active positions (only `MsgDeleteTier` does), so a governance update to an existing tier with many live positions propagates the oversized duration to every future `TriggerExitFromTier` call on those positions.

---

### Impact Explanation

**High.** Any user who locks tokens into the affected tier and calls `MsgTriggerExitFromTier` after the governance update will have their staked tokens locked inside the `x/tieredrewards` module for the duration of `ExitDuration`. With `ExitDuration = math.MaxInt64`, the tokens are effectively permanently irrecoverable. The corrupted value is `Position.ExitUnlockAt`, which gates both `MsgTierUndelegate` and `MsgExitTierWithDelegation`. The delegated principal (staked tokens) held at each position's per-position delegator address is the asset at risk.

---

### Likelihood Explanation

**Low.** Exploiting this requires a governance proposal (`MsgAddTier` or `MsgUpdateTier`) to pass with an excessively large `ExitDuration`. This mirrors the external report's scenario exactly: a fat-finger or misconfiguration by the privileged actor (governance in this chain, the owner in the NFTLootbox). The absence of an upper-bound guard means there is no protocol-level safety net to catch such a mistake before it is committed to state.

---

### Recommendation

Add an upper-bound check for `ExitDuration` in `Tier.Validate()` in `x/tieredrewards/types/tier.go`. Define a protocol constant (e.g., `MaxExitDuration = 10 * 365 * 24 * time.Hour`) and reject any tier whose `ExitDuration` exceeds it:

```go
const MaxExitDuration = 10 * 365 * 24 * time.Hour

if t.ExitDuration > MaxExitDuration {
    return fmt.Errorf("exit duration must not exceed %s: got %s", MaxExitDuration, t.ExitDuration)
}
```

---

### Proof of Concept

1. Governance submits and passes `MsgAddTier` (or `MsgUpdateTier` on an existing tier with active positions) with:
   ```json
   { "id": 1, "exit_duration": "9223372036854775807ns", "bonus_apy": "0.04", "min_lock_amount": "1000000" }
   ```
2. `Tier.Validate()` passes because `ExitDuration > 0`. The tier is stored via `SetTier`.
3. A user calls `MsgLockTier` to lock tokens into tier 1.
4. The user calls `MsgTriggerExitFromTier` on their position.
5. `TriggerExitFromTier` reads `tier.ExitDuration = math.MaxInt64 ns` and sets `pos.ExitUnlockAt = blockTime + 292 years`.
6. The user attempts `MsgTierUndelegate` or `MsgExitTierWithDelegation`. Both fail with `ErrExitLockDurationNotReached` because `block_time < ExitUnlockAt`.
7. The user's staked tokens remain locked in the module's per-position delegator account with no reachable exit path.

**Exact corrupted value:** `Position.ExitUnlockAt` — set to `blockTime + math.MaxInt64 ns`, making both `MsgTierUndelegate` and `MsgExitTierWithDelegation` permanently unreachable. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** x/tieredrewards/types/tier.go (L10-17)
```go
func (t Tier) Validate() error {
	if t.Id == 0 {
		return ErrInvalidTierID
	}

	if t.ExitDuration <= 0 {
		return fmt.Errorf("exit duration must be positive")
	}
```

**File:** x/tieredrewards/keeper/msg_server.go (L361-368)
```go
	tier, err := ms.getTier(ctx, pos.TierId)
	if err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	pos.TriggerExit(sdkCtx.BlockTime(), tier.ExitDuration)

```

**File:** x/tieredrewards/types/position.go (L71-74)
```go
func (p *Position) TriggerExit(blockTime time.Time, duration time.Duration) {
	p.ExitTriggeredAt = blockTime
	p.ExitUnlockAt = blockTime.Add(duration)
}
```

**File:** x/tieredrewards/keeper/tier.go (L27-35)
```go
func (k Keeper) SetTier(ctx context.Context, tier types.Tier) error {
	if err := tier.Validate(); err != nil {
		return err
	}
	if err := k.Tiers.Set(ctx, tier.Id, tier); err != nil {
		return errorsmod.Wrapf(err, "%s (tier id %d)", types.ErrTierStore.Error(), tier.Id)
	}
	return nil
}
```

**File:** x/tieredrewards/keeper/msg_server_auth.go (L57-81)
```go
func (ms msgServer) UpdateTier(ctx context.Context, msg *types.MsgUpdateTier) (*types.MsgUpdateTierResponse, error) {
	if err := ms.requireAuthority(msg.Authority); err != nil {
		return nil, err
	}

	oldTier, err := ms.getTier(ctx, msg.Tier.Id)
	if err != nil {
		return nil, err
	}

	if !oldTier.BonusApy.Equal(msg.Tier.BonusApy) {
		if err := ms.claimRewardsAndUpdateTierPositions(ctx, msg.Tier.Id); err != nil {
			return nil, err
		}
	}

	if err := ms.SetTier(ctx, msg.Tier); err != nil {
		return nil, err
	}

	if err := ms.emitTierChangedEvent(ctx, types.TierChangeAction_TIER_CHANGE_ACTION_UPDATE, msg.Tier); err != nil {
		return nil, err
	}

	return &types.MsgUpdateTierResponse{}, nil
```
