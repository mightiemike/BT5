### Title
Empty Bonus Rewards Pool Permanently Blocks All Exit Paths for Tier Positions - (`x/tieredrewards/keeper/msg_server.go`, `x/tieredrewards/keeper/claim_rewards.go`)

---

### Summary

The `x/tieredrewards` module mandates that bonus rewards are settled (paid out from the `RewardsPoolName` module account) as a prerequisite step inside `MsgTierUndelegate`, `MsgExitTierWithDelegation`, `MsgTierRedelegate`, and `MsgClearPosition`. If the bonus pool balance is insufficient to cover accrued bonus at the time any of these messages is executed, the entire transaction fails atomically and the position cannot be moved. A user who has completed their exit commitment period (e.g., waited 1 year) is therefore unable to undelegate or exit their locked tokens until the pool is externally replenished — with no on-chain guarantee of when or whether that will happen.

---

### Finding Description

Every mutation message that modifies a delegated position calls `claimRewards` before performing the state change. `claimRewards` calls `processEventsAndClaimBonus`, which calls `sufficientBonusPoolBalance`. If the computed bonus exceeds the pool balance, `ErrInsufficientBonusPool` is returned and the entire message reverts.

The call chain in `TierUndelegate`:

```
MsgTierUndelegate
  → ms.claimRewards(ctx, pos)          // line 166 of msg_server.go
      → k.processEventsAndClaimBonus(ctx, &pos)
          → k.sufficientBonusPoolBalance(ctx, bonusCoins)  // REVERTS if pool < bonus
``` [1](#0-0) 

The pool check itself:

```go
if !poolBalance.IsAllGTE(bonus) {
    return errorsmod.Wrapf(types.ErrInsufficientBonusPool, ...)
}
``` [2](#0-1) 

The same `claimRewards` call is present in `TierRedelegate` before the redelegation executes: [3](#0-2) 

The ADR explicitly documents this behavior but frames it as a user-retry situation:

> "User-driven paths (ClaimTierRewards, AddToPosition, Undelegate, Redelegate, ClearPosition): fail atomically. User retries after pool is replenished." [4](#0-3) 

The pool can be drained by two independent mechanisms:
1. The `BeginBlocker` continuously transfers shortfall from the rewards pool to the distribution module for base-rewards top-up.
2. Users claiming rewards via `MsgClaimTierRewards` draw down the pool. [5](#0-4) 

There is no on-chain mechanism to bypass the bonus settlement requirement when a user only wants to exit. The `MsgWithdrawFromTier` message (which does not claim rewards) is only reachable after `MsgTierUndelegate` has already succeeded — so the lock occurs one step earlier, before unbonding can even begin. [6](#0-5) 

---

### Impact Explanation

A user who has satisfied the full exit commitment (e.g., 1-year lock-up) and submits `MsgTierUndelegate` or `MsgExitTierWithDelegation` will have their transaction revert with `ErrInsufficientBonusPool` if the pool is empty. Their tokens remain locked inside the tier module's per-position delegator address, inaccessible to the owner. There is no alternative exit path that bypasses the bonus settlement step. The lock persists indefinitely until an external actor replenishes the pool — a governance or operational action with no guaranteed timeline.

The corrupted invariant: **a user who has completed their exit commitment must be able to retrieve their principal**. When the pool is empty, this invariant is broken. [7](#0-6) 

---

### Likelihood Explanation

The `BeginBlocker` drains the pool every block when `TargetBaseRewardsRate > 0`, independent of user activity. As the number of tier positions grows, the aggregate accrued bonus grows proportionally. A pool that was sufficient at deposit time may be insufficient months later when a long-duration position attempts to exit. The condition is reachable by any ordinary delegator submitting a standard `MsgTierUndelegate` transaction — no privileged access required. [8](#0-7) 

---

### Recommendation

Decouple the bonus settlement requirement from the exit path. Specifically:

1. Allow `MsgTierUndelegate` and `MsgExitTierWithDelegation` to proceed even when the pool cannot cover the accrued bonus. Record the unpaid bonus as a debt on the position (or forfeit it with user consent), and complete the undelegation/exit regardless.
2. Alternatively, cap the bonus settlement to whatever the pool can currently pay (partial payout), and allow the exit to proceed with the remainder forfeited or deferred.
3. Maintain a minimum reserve in the rewards pool specifically sized to cover worst-case exit scenarios across all active positions.

The `BeforeRedelegationSlashed` hook already implements the correct pattern — it forfeits bonus silently if the pool is insufficient rather than halting the operation: [9](#0-8) 

The same "forfeit-on-insufficient-pool" logic should be applied to the user-facing exit messages.

---

### Proof of Concept

1. User calls `MsgLockTier` with a 1-year exit duration and a non-zero `BonusApy` tier.
2. One year passes. The user calls `MsgTriggerExitFromTier`. Exit commitment elapses.
3. During this period, the `BeginBlocker` drains the rewards pool to zero for base-rewards top-up, or other users exhaust it via `MsgClaimTierRewards`.
4. User submits `MsgTierUndelegate`. The message calls `claimRewards` → `processEventsAndClaimBonus` → `sufficientBonusPoolBalance`. Pool balance is 0, accrued bonus is > 0. Returns `ErrInsufficientBonusPool`. Transaction reverts.
5. User submits `MsgExitTierWithDelegation`. Same code path. Same revert.
6. User's principal remains locked in the tier module's delegator address. No exit path is available until the pool is externally replenished. [10](#0-9) [11](#0-10)

### Citations

**File:** x/tieredrewards/keeper/msg_server.go (L152-169)
```go
func (ms msgServer) TierUndelegate(ctx context.Context, msg *types.MsgTierUndelegate) (*types.MsgTierUndelegateResponse, error) {
	if err := msg.Validate(); err != nil {
		return nil, err
	}

	pos, err := ms.getPositionState(ctx, msg.PositionId)
	if err != nil {
		return nil, err
	}

	if err := ms.validateUndelegatePosition(ctx, pos, msg.Owner); err != nil {
		return nil, err
	}

	pos, _, _, err = ms.claimRewards(ctx, pos)
	if err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/msg_server.go (L229-232)
```go
	pos, _, _, err = ms.claimRewards(ctx, pos)
	if err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/bonus_rewards.go (L48-61)
```go
func (k Keeper) sufficientBonusPoolBalance(ctx context.Context, bonus sdk.Coins) error {
	if bonus.IsZero() {
		return nil
	}

	poolAddr := k.accountKeeper.GetModuleAddress(types.RewardsPoolName)
	poolBalance := k.bankKeeper.GetAllBalances(ctx, poolAddr)
	if !poolBalance.IsAllGTE(bonus) {
		return errorsmod.Wrapf(types.ErrInsufficientBonusPool,
			"bonus: %s, pool balance: %s",
			bonus.String(), poolBalance.String())
	}

	return nil
```

**File:** doc/architecture/adr-006.md (L62-71)
```markdown
### Exit (two paths after exit commitment)

1. **Trigger exit** -- `MsgTriggerExitFromTier`. Sets `ExitTriggeredAt` and `ExitUnlockAt`. Bonus continues accruing until `ExitUnlockAt`.

After exit commitment elapses (`block_time >= ExitUnlockAt`), the user chooses one of two paths:

2a. **Exit with delegation (instant)** -- `MsgExitTierWithDelegation(position_id, amount)`. Transfers the module's delegation back to the owner on the same validator (Unbond + Delegate, no unbonding queue). Claims rewards first. Supports partial exits: only the specified amount is transferred; position is reduced accordingly but position size needs to still be at least min lock amount. If the full amount is transferred, the position is deleted. Requires the validator to be bonded.

2b. **Undelegate + Withdraw (21-day wait)** -- `MsgTierUndelegate` claims rewards and starts the staking unbonding period. After unbonding completes, `MsgWithdrawFromTier` transfers `Amount` to the owner and deletes the position.

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

**File:** x/tieredrewards/types/errors.go (L17-17)
```go
	ErrInsufficientBonusPool            = errors.Register(ModuleName, 12, "insufficient bonus pool balance")
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L87-103)
```go
func (k Keeper) claimRewards(ctx context.Context, pos types.PositionState) (types.PositionState, sdk.Coins, sdk.Coins, error) {
	if !pos.IsDelegated() {
		return pos, sdk.NewCoins(), sdk.NewCoins(), nil
	}

	base, err := k.claimBaseRewards(ctx, pos)
	if err != nil {
		return types.PositionState{}, nil, nil, err
	}

	bonus, err := k.processEventsAndClaimBonus(ctx, &pos)
	if err != nil {
		return types.PositionState{}, nil, nil, err
	}

	return pos, base, bonus, nil
}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L228-232)
```go
	bonusCoins := sdk.NewCoins(sdk.NewCoin(bondDenom, totalBonus))

	if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/slash.go (L54-64)
```go
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
