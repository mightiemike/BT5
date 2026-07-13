### Title
`MsgUpdateTier` Governance Execution Permanently Blocked by Unbounded Position Sweep and Bonus Pool Insufficiency — (`x/tieredrewards/keeper/claim_rewards.go`)

---

### Summary

When a governance proposal calls `MsgUpdateTier` to change a tier's `BonusApy`, the handler unconditionally sweeps **all** positions in that tier via `claimRewardsAndUpdateTierPositions`. This unbounded loop pays out each position's accrued bonus sequentially. If the bonus pool is insufficient to cover any single position's accrued bonus, the entire governance proposal fails atomically. Because the transaction rolls back, no positions are settled and the tier APY cannot be updated. This creates a deadlock: an empty pool prevents reducing APY obligations, while high APY obligations drain the pool faster.

---

### Finding Description

`UpdateTier` in `msg_server_auth.go` calls `claimRewardsAndUpdateTierPositions` whenever `BonusApy` changes: [1](#0-0) 

`claimRewardsAndUpdateTierPositions` fetches **all** position IDs for the tier and iterates them without any bound: [2](#0-1) 

For each position, `processEventsAndClaimBonus` is called. Inside that function, after computing the accrued bonus, it checks pool sufficiency and immediately pays out: [3](#0-2) 

The pool sufficiency check is: [4](#0-3) 

Because each position's bonus is paid out before the next position is processed, the pool balance decreases with each iteration. If the pool is exhausted mid-sweep (or was already insufficient), `ErrInsufficientBonusPool` is returned, the entire `UpdateTier` transaction fails, and all state changes roll back. The tier APY is not updated.

The ADR explicitly documents this failure mode for user-driven paths but does not address the governance path: [5](#0-4) 

The test `TestUpdateTier_BonusApyChange_InsufficientPool` confirms this failure is real and expected to block the update: [6](#0-5) 

---

### Impact Explanation

**Governance liveness failure / contribution to protocol insolvency:**

1. The bonus pool depletes naturally as users claim rewards.
2. A governance proposal to reduce `BonusApy` (to reduce future obligations) calls `claimRewardsAndUpdateTierPositions`.
3. If the pool cannot cover all accrued bonuses for all positions in the tier, the proposal fails.
4. The APY stays high, obligations keep accruing, and the pool remains empty.
5. Every subsequent governance attempt to reduce APY also fails — a permanent deadlock.

**Unbounded gas consumption:**

As the number of positions in a tier grows, the gas cost of `claimRewardsAndUpdateTierPositions` grows linearly. Cosmos SDK governance proposals execute within a gas limit. With enough positions, the proposal will always fail with out-of-gas regardless of pool balance, permanently blocking any `BonusApy` change for that tier.

The exact corrupted invariant: the `BonusApy` field of a `Tier` object cannot be updated to its intended governance-approved value, and accrued bonus rewards for all positions in the tier are permanently frozen in a partially-settled state.

---

### Likelihood Explanation

- The bonus pool is funded externally and is expected to be empty between replenishments — this is a normal operational state, not an edge case.
- Any governance proposal to change `BonusApy` submitted during a low-pool window will fail.
- As the protocol grows and more users lock into a tier, the gas exhaustion path becomes increasingly likely regardless of pool balance.
- The governance proposal submission and voting period (days to weeks) means the pool state at execution time is unpredictable at proposal time.

---

### Recommendation

1. **Decouple reward settlement from tier parameter updates.** Do not require settling all positions before changing `BonusApy`. Instead, snapshot the old APY on each position's `LastBonusAccrual` checkpoint lazily at claim time (similar to how `LastEventSeq` already works).

2. **If settlement before APY change is required**, add a paginated settlement message (e.g., `MsgSettleTierPositions(tier_id, start, limit)`) that allows settling positions in batches before the governance proposal executes, and gate `UpdateTier` on a "fully settled" flag rather than doing the sweep inline.

3. **Bound the sweep.** At minimum, add a hard cap on the number of positions processed per `UpdateTier` call and return an error if the tier has more positions than the cap, forcing the operator to use a paginated path.

---

### Proof of Concept

```
Preconditions:
  - Tier 1 exists with BonusApy = 5%
  - 100 positions are locked in Tier 1, each with 30 days of accrued bonus
  - Bonus pool balance = 0 (depleted by prior claims)

Attack path:
  1. Governance submits MsgUpdateTier{Id: 1, BonusApy: 2%}  // reduce obligations
  2. Proposal passes voting period
  3. Execution calls UpdateTier → claimRewardsAndUpdateTierPositions(1)
  4. Position 1: processEventsAndClaimBonus → sufficientBonusPoolBalance fails
     → returns ErrInsufficientBonusPool
  5. Entire UpdateTier tx fails; tier APY remains at 5%
  6. Obligations continue accruing at 5%; pool remains empty
  7. Repeat governance proposals all fail identically → permanent deadlock

Alternatively (gas exhaustion path):
  - Tier 1 has 50,000 positions
  - Any MsgUpdateTier with changed BonusApy exceeds block gas limit
  - Tier APY is permanently frozen regardless of pool balance
```

### Citations

**File:** x/tieredrewards/keeper/msg_server_auth.go (L67-71)
```go
	if !oldTier.BonusApy.Equal(msg.Tier.BonusApy) {
		if err := ms.claimRewardsAndUpdateTierPositions(ctx, msg.Tier.Id); err != nil {
			return nil, err
		}
	}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L50-80)
```go
func (k Keeper) claimRewardsAndUpdateTierPositions(ctx context.Context, tierId uint32) error {
	ids, err := k.getPositionsIdsByTier(ctx, tierId)
	if err != nil {
		return err
	}
	if len(ids) == 0 {
		return nil
	}

	for _, id := range ids {
		pos, err := k.getPositionState(ctx, id)
		if err != nil {
			return err
		}
		if !pos.IsDelegated() {
			continue
		}

		if _, err := k.claimBaseRewards(ctx, pos); err != nil {
			return err
		}
		if _, err := k.processEventsAndClaimBonus(ctx, &pos); err != nil {
			return err
		}
		if err := k.setPosition(ctx, pos.Position, nil); err != nil {
			return err
		}
	}

	return nil
}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L228-241)
```go
	bonusCoins := sdk.NewCoins(sdk.NewCoin(bondDenom, totalBonus))

	if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
		return nil, err
	}

	ownerAddr, err := sdk.AccAddressFromBech32(pos.Owner)
	if err != nil {
		return nil, errorsmod.Wrap(sdkerrors.ErrInvalidAddress, "invalid owner address")
	}

	if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.RewardsPoolName, ownerAddr, bonusCoins); err != nil {
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

**File:** doc/architecture/adr-006.md (L293-295)
```markdown
**Insufficient pool handling:**
- **User-driven paths** (ClaimTierRewards, AddToPosition, Undelegate, Redelegate, ClearPosition): fail atomically. User retries after pool is replenished.

```

**File:** x/tieredrewards/keeper/msg_server_auth_test.go (L339-366)
```go
func (s *KeeperSuite) TestUpdateTier_BonusApyChange_InsufficientPool() {
	lockAmount := sdkmath.NewInt(sdk.DefaultPowerReduction.Int64())
	pos := s.setupNewTierPosition(lockAmount, false)
	valAddr := sdk.MustValAddressFromBech32(pos.Delegation.ValidatorAddress)

	s.setValidatorCommission(valAddr, sdkmath.LegacyZeroDec())

	// Advance time so bonus accrues.
	s.ctx = s.ctx.WithBlockHeight(s.ctx.BlockHeight() + 1)
	s.ctx = s.ctx.WithBlockTime(s.ctx.BlockTime().Add(30 * 24 * time.Hour))

	// Update tier with new BonusApy — should fail due to insufficient pool.
	tier := newTestTier(1)
	initialBonusApy := tier.BonusApy
	updatedBonusApy := sdkmath.LegacyNewDecWithPrec(8, 2)
	tier.BonusApy = updatedBonusApy
	msgServer := keeper.NewMsgServerImpl(s.keeper)
	_, err := msgServer.UpdateTier(s.ctx, &types.MsgUpdateTier{
		Authority: s.keeper.GetAuthority(),
		Tier:      tier,
	})
	s.Require().ErrorIs(err, types.ErrInsufficientBonusPool)

	// Tier should NOT have been updated (tx failed).
	got, err := s.keeper.GetTier(s.ctx, 1)
	s.Require().NoError(err)
	s.Require().True(initialBonusApy.Equal(got.BonusApy), "tier should still have old APY")
}
```
