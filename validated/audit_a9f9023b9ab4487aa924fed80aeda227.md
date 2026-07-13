The vulnerability is real. Here is the complete trace:

---

### Title
Migration Aborts with `ErrInsufficientBonusPool` When Bonus Pool Is Drained Before v8 Upgrade — (`x/tieredrewards/migrations/v2/migrate.go`)

### Summary

The v8 migration's `exitVestedAccountsPositions` calls `ForceFullExitWithDelegation` for every vesting-owned tier position. That function calls `claimRewards` → `processEventsAndClaimBonus` → `sufficientBonusPoolBalance`. If the bonus pool is empty and the position has accrued bonus, `ErrInsufficientBonusPool` is returned and propagated all the way up through `Migrate()` without being swallowed, causing the upgrade handler to fail and halting the chain. The identical error is **explicitly swallowed** in `slashRedelegationPosition` with the comment *"Deliberately forgo bonus rewards if pool is insufficient to prevent chain halt"* — but that protection is absent in the migration path.

### Finding Description

**Call chain:**

```
Migrate()
  → exitVestedAccountsPositions()          [migrate.go:99]
    → ForceFullExitWithDelegation()        [force_exit.go:37]
      → claimRewards()                     [claim_rewards.go:97]
        → processEventsAndClaimBonus()     [claim_rewards.go:230]
          → sufficientBonusPoolBalance()   → ErrInsufficientBonusPool
```

**`ForceFullExitWithDelegation`** propagates the error unconditionally: [1](#0-0) 

**`exitVestedAccountsPositions`** propagates it again: [2](#0-1) 

**`Migrate`** propagates it to the upgrade handler: [3](#0-2) 

**`sufficientBonusPoolBalance`** returns the error when pool balance < bonus owed: [4](#0-3) 

**Contrast — `slashRedelegationPosition` explicitly swallows the same error:** [5](#0-4) 

The comment there reads *"Deliberately forgo bonus rewards if pool is insufficient to prevent chain halt."* No equivalent guard exists in `ForceFullExitWithDelegation`.

### Impact Explanation

A Cosmos SDK upgrade handler that returns an error causes the upgrade to fail. In CometBFT, a failed upgrade handler panics the node at the upgrade height, halting the chain. Every validator hits the same state, so the chain cannot make progress until the binary is patched and re-deployed. This is a **chain halt at upgrade height**.

### Likelihood Explanation

The preconditions are all reachable through normal production paths:

1. **Vesting account with a tier position** — team/investor vesting accounts are standard; `MsgLockTier` is open to any account including vesting accounts (the `ErrVestingAccountNotAllowed` guard was added for new actions post-v8, not pre-existing positions).
2. **Accrued bonus** — any position delegated to a bonded validator with `LastBonusAccrual` in the past will have non-zero `totalBonus` computed in `processEventsAndClaimBonus`.
3. **Empty bonus pool** — the pool is drained by normal user reward claims and by the BeginBlocker top-up mechanism. It is entirely plausible (and easy to arrange deliberately) that the pool reaches zero before the upgrade block.

An attacker who knows the upgrade height can drain the pool by claiming their own rewards in the blocks immediately before the upgrade, guaranteeing the pool is empty when `Migrate()` runs.

### Recommendation

Apply the same guard used in `slashRedelegationPosition` inside `ForceFullExitWithDelegation`:

```go
posState, baseRewards, bonusRewards, err := k.claimRewards(ctx, posState)
if err != nil {
    if errors.Is(err, types.ErrInsufficientBonusPool) {
        // Forgo bonus rewards to prevent chain halt during migration.
        logger.Error("force-exit: insufficient bonus pool, forgoing bonus",
            "position_id", posID, "error", err)
        // re-fetch posState without bonus claim, or proceed with zero bonus
    } else {
        return fmt.Errorf("claim rewards for position %d: %w", posID, err)
    }
}
```

Alternatively, split `claimRewards` so base rewards are always claimed and bonus is attempted with a fallback to skip on `ErrInsufficientBonusPool`, consistent with the documented invariant in `adr-006.md` §6.

### Proof of Concept

1. Create a vesting account `owner`.
2. Call `MsgLockTier` to create a tier position delegated to a bonded validator.
3. Advance block time so `LastBonusAccrual` is in the past and bonus accrues.
4. Drain the `tieredrewards` rewards pool module account to zero via other accounts claiming rewards.
5. Call `Migrate1to2` (the v2 migration entry point).
6. Assert the return value `errors.Is(err, types.ErrInsufficientBonusPool)` — the migration aborts.
7. Contrast: call `slashRedelegationPosition` with the same pool state — it returns `nil` (bonus silently forfeited).

The exact error path is confirmed by:
- [6](#0-5) 
- [1](#0-0) 
- [2](#0-1)

### Citations

**File:** x/tieredrewards/keeper/force_exit.go (L37-40)
```go
	posState, baseRewards, bonusRewards, err := k.claimRewards(ctx, posState)
	if err != nil {
		return fmt.Errorf("claim rewards for position %d: %w", posID, err)
	}
```

**File:** x/tieredrewards/migrations/v2/migrate.go (L37-39)
```go
	if err := exitVestedAccountsPositions(ctx, positions, ak, pk); err != nil {
		return fmt.Errorf("exit vested accounts positions: %w", err)
	}
```

**File:** x/tieredrewards/migrations/v2/migrate.go (L97-101)
```go
	for _, posID := range toExit {
		sdkCtx.Logger().Info("v8 migration: force-exit vesting-owned position", "position_id", posID)
		if err := pk.ForceFullExitWithDelegation(ctx, posID); err != nil {
			return fmt.Errorf("force-exit position %d: %w", posID, err)
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

**File:** x/tieredrewards/keeper/claim_rewards.go (L230-232)
```go
	if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
		return nil, err
	}
```
