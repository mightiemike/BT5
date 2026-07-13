### Title
Silent Bonus Reward Forfeiture During Redelegation Slash When Pool Is Insufficient — (File: x/tieredrewards/keeper/slash.go)

### Summary
The `BeforeRedelegationSlashed` staking hook in `x/tieredrewards/keeper/slash.go` silently discards a position's entire accrued bonus reward when the `RewardsPoolName` module account cannot cover the computed payout. Every user-driven message path fails atomically and allows retry; this hook path permanently erases the earned reward with no recourse for the position owner.

### Finding Description
When a user calls `MsgTierRedelegate`, `TierRedelegate` in `msg_server.go` stores a `RedelegationMappings[unbondingID → positionID]` entry and the staking module begins a redelegation period. [1](#0-0) 

If the source validator is slashed during that period, the staking module fires the `BeforeRedelegationSlashed` hook. The hook retrieves the affected position via `RedelegationMappings`, then calls `processEventsAndClaimBonus` to settle accrued bonus rewards against the pre-slash shares. [2](#0-1) 

Inside `processEventsAndClaimBonus`, `sufficientBonusPoolBalance` checks whether the pool holds enough tokens: [3](#0-2) 

For every user-driven path (`ClaimTierRewards`, `AddToTierPosition`, `TierUndelegate`, `TierRedelegate`, `ClearPosition`), an `ErrInsufficientBonusPool` error causes the entire transaction to fail atomically, preserving the user's ability to retry after the pool is replenished. [4](#0-3) 

The `BeforeRedelegationSlashed` hook cannot propagate an error without halting the chain. The ADR explicitly documents the consequence: **"Bonus forfeits silently if the pool is insufficient (chain-halt avoidance)."** The hook discards the error, the position's bonus checkpoints (`LastBonusAccrual`, `LastEventSeq`, `LastKnownBonded`) are reset to post-slash values, and the accrued bonus is permanently lost. <cite repo="Thankgoddavid56/chain-main--001" path="doc/architecture/

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

**File:** doc/architecture/adr-006.md (L293-295)
```markdown
**Insufficient pool handling:**
- **User-driven paths** (ClaimTierRewards, AddToPosition, Undelegate, Redelegate, ClearPosition): fail atomically. User retries after pool is replenished.

```

**File:** doc/architecture/adr-006.md (L309-311)
```markdown
| **BeforeValidatorSlashed** | Records `SLASH` event at O(1) with pre-slash `TokensPerShare` snapshot. No position iteration. `x/distribution` handles per-delegator slash accounting natively (each position's delegator records a `ValidatorSlashEvent`); unbonding-delegation and standard delegation slashes require no tier-side hook because every position is its own staking delegator. |
| **BeforeRedelegationSlashed** | Fires before staking's `Unbond` in `SlashRedelegation`. Routes via `RedelegationMappings[unbondingId]` to the affected position and runs `processEventsAndClaimBonus` against **pre-slash** shares. Base rewards auto-withdraw inside distribution's `BeforeDelegationSharesModified` (still fired by the subsequent `Unbond`). On full slash, `pos.Delegation` is set to nil and checkpoints reset. Bonus forfeits silently if the pool is insufficient (chain-halt avoidance). |
| **AfterRedelegationCompleted** | Iterates the hook's `completedIds` and removes each matching row from `RedelegationMappings`. |
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
