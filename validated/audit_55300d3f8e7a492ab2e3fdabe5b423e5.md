### Title
Mandatory Reward Settlement in Exit-Path Handlers Blocks `MsgTierUndelegate` and `MsgExitTierWithDelegation` When Bonus Pool Is Empty, Locking User Funds - (File: `x/tieredrewards/keeper/msg_server.go`)

---

### Summary

`MsgTierUndelegate` and `MsgExitTierWithDelegation` both unconditionally call `claimRewards` before performing the exit operation. `claimRewards` calls `processEventsAndClaimBonus`, which hard-fails with `ErrInsufficientBonusPool` when the bonus pool cannot cover accrued bonus. Because the error propagates atomically, the entire exit transaction is rejected. A user who has completed the full exit commitment period (e.g., 1 year) and is entitled to undelegate their locked tokens is blocked from doing so for as long as the pool remains empty, leaving their principal locked inside the module account.

---

### Finding Description

**Root cause — `TierUndelegate`:**

In `TierUndelegate`, `claimRewards` is called before the staking `Undelegate` call:

```go
pos, _, _, err = ms.claimRewards(ctx, pos)
if err != nil {
    return nil, err   // ← entire tx aborts here
}
// ... undelegate never reached
completionTime, _, err := ms.undelegate(ctx, delAddr, valAddr, pos.Delegation.Shares)
``` [1](#0-0) 

**Root cause — `ExitTierWithDelegation`:**

The same pattern exists in `ExitTierWithDelegation`:

```go
pos, _, _, err = ms.claimRewards(ctx, pos)
if err != nil {
    return nil, err   // ← entire tx aborts here
}
// ... transferDelegationFromPosition never reached
``` [2](#0-1) 

**Pool check inside `processEventsAndClaimBonus`:**

```go
if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
    return nil, err
}
``` [3](#0-2) 

When `bonusCoins > pool balance`, `sufficientBonusPoolBalance` returns `ErrInsufficientBonusPool`. This error propagates all the way back through `claimRewards` → `TierUndelegate` / `ExitTierWithDelegation`, aborting the transaction before the staking unbond or delegation transfer is executed.

The ADR documents this as intentional for user-driven paths:


> "User-driven paths (ClaimTierRewards, AddToPosition, Undelegate, Redelegate, ClearPosition): fail atomically. User retries after pool is replenished." [4](#0-3) 

However, this design conflates two distinct operations: **settling accrued rewards** (which requires pool funds) and **releasing the principal** (which requires no pool funds). The exit-path messages are explicitly listed as allowed under `CloseOnly` tiers precisely because they must always succeed for users to reclaim their assets: [5](#0-4) 

The same principle — exit paths must not be blocked by a module-level state condition — is violated here when the pool is empty.

---

### Impact Explanation

A position owner who has:
1. Locked tokens into a tier,
2. Triggered exit and waited the full `ExitDuration` (e.g., 365 days),
3. Satisfied all validation checks in `validateUndelegatePosition`,

…cannot undelegate their **principal** if the bonus pool is empty at the time of the call. The tokens remain escrowed in the module account (`tieredrewards` module account) indefinitely. The user's only recourse is to wait for governance to replenish the pool — a dependency entirely outside the user's control.

The corrupted invariant: the module holds user principal in escrow and must release it once the exit commitment elapses, regardless of the pool's ability to pay bonus rewards.

---

### Likelihood Explanation

The bonus pool can be empty or insufficient in realistic production scenarios:

- Many users simultaneously claiming rewards drains the pool faster than the `BeginBlocker` top-up replenishes it.
- Governance delays or disputes over pool replenishment.
- The `TargetBaseRewardsRate` BeginBlocker itself draws from the same `RewardsPoolName` module account for base-reward top-ups, competing with bonus payouts. [6](#0-5) 

Any position owner with accrued bonus (i.e., any delegated position on a bonded validator after any non-zero time) will trigger the pool check on `TierUndelegate`. The trigger is reachable by any unprivileged account via a standard `MsgTierUndelegate` transaction.

---

### Recommendation

Decouple reward settlement from the exit operation in `TierUndelegate` and `ExitTierWithDelegation`. Two options:

1. **Skip bonus claim when pool is insufficient on exit paths**: If `processEventsAndClaimBonus` returns `ErrInsufficientBonusPool`, advance the position's checkpoints (so no double-claim later) but proceed with the undelegation/transfer. The user forfeits the bonus they cannot be paid — consistent with the existing behavior in `BeforeRedelegationSlashed`. [7](#0-6) 

2. **Separate the two messages**: Allow undelegation without mandatory reward settlement; let the user claim rewards independently via `MsgClaimTierRewards` before or after undelegating.

---

### Proof of Concept

1. User locks tokens into tier 1 with `MsgLockTier`, triggering exit immediately.
2. Time advances past `ExitDuration` (365 days). All validation in `validateUndelegatePosition` passes.
3. Bonus pool is empty (e.g., drained by prior claims or never funded).
4. User submits `MsgTierUndelegate`.
5. `claimRewards` → `processEventsAndClaimBonus` → `sufficientBonusPoolBalance` returns `ErrInsufficientBonusPool`.
6. Transaction aborts. `undelegate` is never called. Position remains delegated. Tokens remain locked.
7. User retries — same result. Tokens are inaccessible until governance replenishes the pool.

The existing test `TestMsgTierUndelegate_Basic` funds the pool before undelegating (`s.fundRewardsPool`), masking this failure mode: [8](#0-7) 

A test without `fundRewardsPool` after time advances would reproduce the lockup.

### Citations

**File:** x/tieredrewards/keeper/msg_server.go (L166-183)
```go
	pos, _, _, err = ms.claimRewards(ctx, pos)
	if err != nil {
		return nil, err
	}

	srcValidator := pos.Delegation.ValidatorAddress
	valAddr, err := sdk.ValAddressFromBech32(srcValidator)
	if err != nil {
		return nil, err
	}

	delAddr, err := sdk.AccAddressFromBech32(pos.DelegatorAddress)
	if err != nil {
		return nil, errorsmod.Wrap(sdkerrors.ErrInvalidAddress, "invalid delegator address")
	}

	completionTime, _, err := ms.undelegate(ctx, delAddr, valAddr, pos.Delegation.Shares)
	if err != nil {
```

**File:** x/tieredrewards/keeper/msg_server.go (L532-535)
```go
	pos, _, _, err = ms.claimRewards(ctx, pos)
	if err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L230-232)
```go
	if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
		return nil, err
	}
```

**File:** doc/architecture/adr-006.md (L74-77)
```markdown
### CloseOnly Tiers

When a tier is marked `CloseOnly` by governance, these messages are **blocked**: LockTier, CommitDelegation, AddToPosition, TierRedelegate, ClearPosition. **Allowed**: TriggerExit, TierUndelegate, WithdrawFromTier, ClaimTierRewards, ExitTierWithDelegation. This lets governance sunset a tier while existing positions close out.

```

**File:** doc/architecture/adr-006.md (L293-295)
```markdown
**Insufficient pool handling:**
- **User-driven paths** (ClaimTierRewards, AddToPosition, Undelegate, Redelegate, ClearPosition): fail atomically. User retries after pool is replenished.

```

**File:** doc/architecture/adr-006.md (L296-299)
```markdown
### BeginBlocker: Base Rewards Top-Up

If `TargetBaseRewardsRate > 0`, the BeginBlocker computes the target per-block staker reward, compares it to the fee collector balance (after community tax), and transfers any shortfall from the rewards pool to the distribution module, allocated pro-rata by validator power.

```

**File:** doc/architecture/adr-006.md (L309-311)
```markdown
| **BeforeValidatorSlashed** | Records `SLASH` event at O(1) with pre-slash `TokensPerShare` snapshot. No position iteration. `x/distribution` handles per-delegator slash accounting natively (each position's delegator records a `ValidatorSlashEvent`); unbonding-delegation and standard delegation slashes require no tier-side hook because every position is its own staking delegator. |
| **BeforeRedelegationSlashed** | Fires before staking's `Unbond` in `SlashRedelegation`. Routes via `RedelegationMappings[unbondingId]` to the affected position and runs `processEventsAndClaimBonus` against **pre-slash** shares. Base rewards auto-withdraw inside distribution's `BeforeDelegationSharesModified` (still fired by the subsequent `Unbond`). On full slash, `pos.Delegation` is set to nil and checkpoints reset. Bonus forfeits silently if the pool is insufficient (chain-halt avoidance). |
| **AfterRedelegationCompleted** | Iterates the hook's `completedIds` and removes each matching row from `RedelegationMappings`. |
```

**File:** x/tieredrewards/keeper/msg_server_undelegate_test.go (L17-34)
```go
func (s *KeeperSuite) TestMsgTierUndelegate_Basic() {
	pos := s.setupNewTierPosition(sdkmath.NewInt(1000), true)
	delAddr := sdk.MustAccAddressFromBech32(pos.Owner)
	valAddr := sdk.MustValAddressFromBech32(pos.Delegation.ValidatorAddress)
	_, bondDenom := s.getStakingData()
	msgServer := keeper.NewMsgServerImpl(s.keeper)
	// Create delegated + exit-triggered position

	// Fund the rewards pool so bonus claim doesn't fail
	s.fundRewardsPool(sdkmath.NewInt(10000), bondDenom)

	s.advancePastExitDuration()
	resp, err := msgServer.TierUndelegate(s.ctx, &types.MsgTierUndelegate{
		Owner:      delAddr.String(),
		PositionId: pos.Id,
	})
	s.Require().NoError(err)
	s.Require().False(resp.CompletionTime.IsZero())
```
