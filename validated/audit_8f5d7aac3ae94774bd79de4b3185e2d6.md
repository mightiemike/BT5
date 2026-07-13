### Title
Vesting Account Restriction Bypassed via `MsgAddToTierPosition` — (`x/tieredrewards/keeper/msg_validate.go`)

### Summary

The `x/tieredrewards` module blocks vesting accounts from creating new tier positions via `MsgLockTier` and `MsgCommitDelegationToTier` by calling `validateNonVestingAccount` inside `validateNewPosition`. However, `MsgAddToTierPosition` — which also locks tokens into a tier by sending funds from the owner and delegating them — does not call `validateNonVestingAccount`. A vesting account that holds an existing position can call `MsgAddToTierPosition` to inject additional tokens into the tier, bypassing the restriction entirely and producing the same stale vesting-delegation-tracking state that the restriction was designed to prevent.

### Finding Description

`validateNewPosition` (called by both `LockTier` and `CommitDelegationToTier`) enforces the vesting-account guard:

```go
func (k Keeper) validateNewPosition(ctx context.Context, owner string, amount math.Int, tier types.Tier) error {
    if err := k.validateNonVestingAccount(ctx, owner); err != nil {
        return err
    }
    ...
}
``` [1](#0-0) 

`validateAddToPosition`, used by `MsgAddToTierPosition`, performs no such check:

```go
func (k Keeper) validateAddToPosition(ctx context.Context, pos types.PositionState, owner string) error {
    if !pos.IsOwner(owner) { return types.ErrNotPositionOwner }
    if !pos.IsDelegated()  { return types.ErrPositionNotDelegated }
    if pos.HasTriggeredExit() { return types.ErrPositionTriggeredExit }
    tier, err := k.getTier(ctx, pos.TierId)
    ...
    if tier.IsCloseOnly() { return types.ErrTierIsCloseOnly }
    return nil
}
``` [2](#0-1) 

`AddToTierPosition` then calls `lockFunds` (a `bankKeeper.SendCoins` from owner → position delegator address) followed by `delegate`: [3](#0-2) 

The ADR-006 reference table confirms the asymmetry: `MsgLockTier` and `MsgCommitDelegationToTier` list "owner is not a vesting account" as a key validation, while `MsgAddToTierPosition` lists only "Delegated; not exiting; tier not close-only": [4](#0-3) 

The reason the restriction exists is documented in `force_exit.go`: `transferDelegationFromPosition` uses `subtractAccount=false`, which skips the `TrackDelegation` bank hook. For `LockTier`-origin positions this leaves `DelegatedVesting + DelegatedFree` stale-low on exit, allowing the vesting account to spend more tokens than the vesting schedule permits: [5](#0-4) 

`AddToTierPosition` follows the identical fund-lock-then-delegate path as `LockTier`, so the same stale-tracking outcome occurs on exit — but the guard is absent.

### Impact Explanation

A vesting account that holds an existing tier position (possible for any position created before the v8 vesting restriction was introduced, if the migration did not fully clear it, or if the chain is running v8 keeper code before the migration executes) can call `MsgAddToTierPosition` to lock additional tokens. On subsequent exit via `MsgExitTierWithDelegation` or `MsgTierUndelegate` + `MsgWithdrawFromTier`, the delegation is returned to the owner with `subtractAccount=false`, leaving `DelegatedVesting + DelegatedFree` stale-low. The vesting module then computes `SpendableCoins` as higher than the vesting schedule allows, enabling the vesting account to spend tokens that should still be locked. This is a direct, unauthorized movement of vesting-locked funds.

### Likelihood Explanation

The trigger requires a vesting account to hold an existing position. After the v8 migration, all vesting-owned positions are force-exited. However, the window is real in two scenarios: (1) the chain is running v8 keeper code during the upgrade block before the migration store migration executes; (2) a force-exit in the migration fails atomically (e.g., due to an insufficient bonus pool), leaving vesting-owned positions alive post-upgrade. The code gap is permanent and will affect any future chain state where a vesting account holds a position.

### Recommendation

Add `validateNonVestingAccount` to `validateAddToPosition`, mirroring the check already present in `validateNewPosition`:

```go
func (k Keeper) validateAddToPosition(ctx context.Context, pos types.PositionState, owner string) error {
+   if err := k.validateNonVestingAccount(ctx, owner); err != nil {
+       return err
+   }
    if !pos.IsOwner(owner) { ... }
    ...
}
``` [2](#0-1) 

### Proof of Concept

1. A vesting account `V` holds an existing tier position (position ID `P`) with 1 000 tokens delegated.
2. `V` submits `MsgAddToTierPosition{Owner: V, PositionId: P, Amount: 5000}`.
3. `validateAddToPosition` passes — no vesting check.
4. `lockFunds` sends 5 000 tokens from `V`'s bank balance to the position's delegator address; `delegate` stakes them. `V.DelegatedVesting` is unchanged.
5. `V` triggers exit and calls `MsgExitTierWithDelegation` (or `TierUndelegate` + `WithdrawFromTier`).
6. `transferDelegationFromPosition` re-delegates 6 000 tokens back to `V` with `subtractAccount=false`, skipping `TrackDelegation`.
7. `V.DelegatedVesting + V.DelegatedFree` is now stale-low by 5 000 tokens.
8. `SpendableCoins(V) = BankBalance(V) - max(VestingCoins - DelegatedVesting, 0)` returns a value 5 000 tokens higher than the vesting schedule permits.
9. `V` spends the excess tokens, bypassing the vesting lock.

### Citations

**File:** x/tieredrewards/keeper/msg_validate.go (L28-42)
```go
func (k Keeper) validateNewPosition(ctx context.Context, owner string, amount math.Int, tier types.Tier) error {
	if err := k.validateNonVestingAccount(ctx, owner); err != nil {
		return err
	}

	if tier.IsCloseOnly() {
		return types.ErrTierIsCloseOnly
	}

	if !tier.MeetsMinLockRequirement(amount) {
		return types.ErrMinLockAmountNotMet
	}

	return nil
}
```

**File:** x/tieredrewards/keeper/msg_validate.go (L105-128)
```go
func (k Keeper) validateAddToPosition(ctx context.Context, pos types.PositionState, owner string) error {
	if !pos.IsOwner(owner) {
		return types.ErrNotPositionOwner
	}

	if !pos.IsDelegated() {
		return types.ErrPositionNotDelegated
	}

	if pos.HasTriggeredExit() {
		return types.ErrPositionTriggeredExit
	}

	tier, err := k.getTier(ctx, pos.TierId)
	if err != nil {
		return err
	}

	if tier.IsCloseOnly() {
		return types.ErrTierIsCloseOnly
	}

	return nil
}
```

**File:** x/tieredrewards/keeper/msg_server.go (L310-325)
```go
	if err := ms.lockFunds(ctx, ownerAddr, delAddr, msg.Amount); err != nil {
		return nil, err
	}

	pos, _, _, err = ms.claimRewards(ctx, pos)
	if err != nil {
		return nil, err
	}

	valAddr, err := sdk.ValAddressFromBech32(pos.Delegation.ValidatorAddress)
	if err != nil {
		return nil, err
	}

	newShares, err := ms.delegate(ctx, delAddr, valAddr, msg.Amount)
	if err != nil {
```

**File:** doc/architecture/adr-006.md (L155-166)
```markdown
| Message | Description | Key Validations |
|---------|-------------|-----------------|
| **MsgLockTier** | Lock tokens + delegate to validator. Optional `trigger_exit_immediately`. | amount >= MinLockAmount; validator bonded; tier not close-only; **owner is not a vesting account** |
| **MsgCommitDelegationToTier** | Transfer existing delegation to tier (no unbonding). Partial allowed. | amount <= user's delegation; amount >= MinLockAmount; tier not close-only; **delegator is not a vesting account** |
| **MsgAddToTierPosition** | Add tokens to existing position. Claims rewards first and delegates new amount. | Delegated; not exiting; tier not close-only |
| **MsgTierRedelegate** | Move delegation to another validator. Claims rewards first. | Exit not elapsed; tier not close-only; Amount > 0; dst != src |
| **MsgTierUndelegate** | Undelegate after exit commitment. Claims rewards first. Clears delegation state immediately. | Exit triggered; exit elapsed; delegated |
| **MsgTriggerExitFromTier** | Start exit commitment. | Not already exiting |
| **MsgClearPosition** | Cancel exit. Settles rewards first. If delegated, resets `LastBonusAccrual` to block_time. No-op if not exiting. | Tier not close-only; if exit elapsed: must be delegated and not unbonding |
| **MsgWithdrawFromTier** | Withdraw tokens + delete position. | Exit triggered; exit elapsed; not delegated; no pending unbonding |
| **MsgClaimTierRewards** | Claim base + bonus rewards for one or more positions. All positions must belong to the signer. | Owner match on all positions; position_ids non-empty, no duplicates, max 500; returns zero per position if not delegated |
| **MsgExitTierWithDelegation** | Transfer delegation back to owner (no unbonding). Supports partial exits. Deletes position if fully exited. | Exit triggered; exit elapsed; delegated; amount > 0; amount <= position amount; validator bonded; no active redelegation; partial exit: remaining >= MinLockAmount |
```

**File:** x/tieredrewards/keeper/force_exit.go (L96-107)
```go
// alignVestingDelegationTracking ensures that for a vesting account owner,
// DelegatedVesting + DelegatedFree matches the actual sum of on-chain
// delegations after a force-exit returns delegation back to the owner.
//
// transferDelegationFromPosition delegates to the owner with subtractAccount=false,
// which skips the bank-side TrackDelegation hook. For LockTier-origin positions
// this leaves DV+DF stale-low; for CommitDelegationToTier-origin positions DV+DF
// was already stale-high pre-migration and the returning delegation closes the
// gap. The diff-based top-up handles both, regardless of position origin or the
// order in which positions are exited.
func (k Keeper) alignVestingDelegationTracking(ctx context.Context, ownerAddr sdk.AccAddress) error {
	logger := k.logger(ctx)
```
