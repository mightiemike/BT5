The key code paths are now fully traced. Let me confirm the exact lines in `positionVotingPower` and `gov_tally.go` that are missing the exit guard.

### Title
Tiered-Rewards Position With Triggered Exit Retains Full Governance Voting Power Until `ExitTierWithDelegation` Is Called — (`x/tieredrewards/keeper/voting_power.go`, `x/tieredrewards/keeper/gov_tally.go`)

---

### Summary

`positionVotingPower` only gates on `IsDelegated()` and never checks `HasTriggeredExit()`. `GetPositionStatesByOwner` returns every position for an owner with no exit-state filter. The custom tally function therefore counts the full delegation shares of a position whose exit has been triggered, letting the owner vote with stake they have already committed to withdraw, and then call `ExitTierWithDelegation` after the proposal closes.

---

### Finding Description

**`positionVotingPower` — missing `HasTriggeredExit()` guard**

```go
// voting_power.go:15-27
func positionVotingPower(pos types.PositionState, bondedVals map[string]v1.ValidatorGovInfo) math.LegacyDec {
    if !pos.IsDelegated() {          // ← only guard; no HasTriggeredExit() check
        return math.LegacyZeroDec()
    }
    ...
    return pos.Delegation.Shares.MulInt(val.BondedTokens).Quo(val.DelegatorShares)
}
``` [1](#0-0) 

**`GetPositionStatesByOwner` — no exit-state filter**

```go
// position_state.go:66-82
func (k Keeper) GetPositionStatesByOwner(...) ([]types.PositionState, error) {
    // returns ALL positions, including those with HasTriggeredExit() == true
    ...
    states = append(states, state)
    ...
}
``` [2](#0-1) 

**`gov_tally.go` — no per-position exit check before accumulating power**

```go
// gov_tally.go:111-127
for _, pos := range positions {
    posPower := positionVotingPower(pos, validators)   // ← exiting positions pass through
    if posPower.IsZero() { continue }
    ...
    totalVotingPower = totalVotingPower.Add(posPower)
}
``` [3](#0-2) 

**`TriggerExitFromTier` — sets exit timestamps, leaves delegation intact**

```go
// msg_server.go:367
pos.TriggerExit(sdkCtx.BlockTime(), tier.ExitDuration)
// delegation is NOT removed; position's delegator account still holds shares
``` [4](#0-3) 

**`ExitTierWithDelegation` — requires `CompletedExitLockDuration`, so withdrawal is deferred**

```go
// msg_validate.go:225-227
if !pos.CompletedExitLockDuration(sdkCtx.BlockTime()) {
    return types.ErrExitLockDurationNotReached
}
``` [5](#0-4) 

The exit lock duration is the window in which the position is simultaneously (a) committed to exit and (b) still fully delegated and fully counted in governance tally.

---

### Impact Explanation

The position's delegation lives under a derived module account (`pos.DelegatorAddress`), not the owner's own account. The custom tally adds the position's delegation power on top of the owner's standard staking power. An owner who has triggered exit retains that extra voting power for the entire exit lock period. After the governance vote closes, they call `ExitTierWithDelegation` and recover the underlying stake. The governance outcome was influenced by stake that is no longer in the system. [6](#0-5) 

---

### Likelihood Explanation

- Exit lock durations are on the order of days to weeks (same order as governance voting periods).
- A governance proposal active during any part of that window is sufficient.
- No privileged role is required; any position owner can call `TriggerExitFromTier`.
- The call sequence is fully reachable through standard `MsgServer` transactions. [7](#0-6) 

---

### Recommendation

Add an exit-state guard in `positionVotingPower`:

```go
func positionVotingPower(pos types.PositionState, bondedVals map[string]v1.ValidatorGovInfo) math.LegacyDec {
    if !pos.IsDelegated() || pos.HasTriggeredExit() {
        return math.LegacyZeroDec()
    }
    ...
}
``` [1](#0-0) 

Alternatively, filter in `GetPositionStatesByOwner` when called from the gov-tally path, or add the check inline in `gov_tally.go` before calling `positionVotingPower`. [8](#0-7) 

---

### Proof of Concept

```
1. Alice holds a large tiered position (posId=1) delegated to valA.
2. Alice calls MsgTriggerExitFromTier{PositionId: 1}.
   → pos.ExitTriggeredAt = T, pos.ExitUnlockAt = T + exit_duration.
   → Delegation from pos.DelegatorAddress to valA is unchanged.
3. A governance proposal is submitted (proposalId=42).
4. Alice calls MsgVote{ProposalId: 42, Option: Yes}.
   → gov_tally calls GetPositionStatesByOwner(alice).
   → positionVotingPower returns full shares (IsDelegated()=true, no exit check).
   → Alice's vote is weighted with both her own staking power AND the position's power.
5. Proposal closes; Alice's vote influenced the outcome.
6. blockTime >= ExitUnlockAt.
7. Alice calls MsgExitTierWithDelegation{PositionId: 1, Amount: full}.
   → validateExitTierWithDelegation passes (CompletedExitLockDuration=true).
   → Delegation transferred back to Alice; position deleted.
8. Alice has recovered her stake; governance was influenced with stake no longer in the system.
``` [9](#0-8) [10](#0-9)

### Citations

**File:** x/tieredrewards/keeper/voting_power.go (L15-27)
```go
func positionVotingPower(
	pos types.PositionState,
	bondedVals map[string]v1.ValidatorGovInfo,
) math.LegacyDec {
	if !pos.IsDelegated() {
		return math.LegacyZeroDec()
	}
	val, ok := bondedVals[pos.Delegation.ValidatorAddress]
	if !ok || val.DelegatorShares.IsZero() {
		return math.LegacyZeroDec()
	}
	return pos.Delegation.Shares.MulInt(val.BondedTokens).Quo(val.DelegatorShares)
}
```

**File:** x/tieredrewards/keeper/position_state.go (L63-83)
```go
// GetPositionStatesByOwner returns each owned position paired with its
// staking delegation (if any).
// Used by gov tally, skip positions that are not found to prevent endblocker halting.
func (k Keeper) GetPositionStatesByOwner(ctx context.Context, owner sdk.AccAddress) ([]types.PositionState, error) {
	ids, err := k.getPositionsIdsByOwner(ctx, owner)
	if err != nil {
		return nil, err
	}
	states := make([]types.PositionState, 0, len(ids))
	for _, id := range ids {
		state, err := k.getPositionState(ctx, id)
		if errors.Is(err, types.ErrPositionNotFound) {
			continue
		}
		if err != nil {
			return nil, err
		}
		states = append(states, state)
	}
	return states, nil
}
```

**File:** x/tieredrewards/keeper/gov_tally.go (L79-127)
```go
			err = sk.IterateDelegations(ctx, voter, func(_ int64, delegation stakingtypes.DelegationI) (stop bool) {
				valAddrStr := delegation.GetValidatorAddr()

				if val, ok := validators[valAddrStr]; ok {
					val.DelegatorDeductions = val.DelegatorDeductions.Add(delegation.GetShares())
					validators[valAddrStr] = val

					votingPower := delegation.GetShares().MulInt(val.BondedTokens).Quo(val.DelegatorShares)
					if err := distributeVotingPower(vote.Options, votingPower, results); err != nil {
						voteWeightErr = fmt.Errorf("invalid vote weight for voter %s: %w", vote.Voter, err)
						return true
					}
					totalVotingPower = totalVotingPower.Add(votingPower)
				}

				return false
			})
			if voteWeightErr != nil {
				return false, voteWeightErr
			}
			if err != nil {
				return false, err
			}

			// Tier-delegated voting power:
			// - compute from position delegation/share state via keeper-level helper
			// - still deduct DelegatedShares from bonded validator second-pass tally
			//   when the validator is present in the gov validator map
			positions, err := tierKeeper.GetPositionStatesByOwner(ctx, voter)
			if err != nil {
				return false, fmt.Errorf("error getting tier positions for %s: %w", vote.Voter, err)
			}
			for _, pos := range positions {
				posPower := positionVotingPower(pos, validators)
				if posPower.IsZero() {
					continue
				}

				valAddr := pos.Delegation.ValidatorAddress
				if val, ok := validators[valAddr]; ok {
					val.DelegatorDeductions = val.DelegatorDeductions.Add(pos.Delegation.Shares)
					validators[valAddr] = val
				}

				if err := distributeVotingPower(vote.Options, posPower, results); err != nil {
					return false, fmt.Errorf("invalid vote weight for voter %s: %w", vote.Voter, err)
				}
				totalVotingPower = totalVotingPower.Add(posPower)
			}
```

**File:** x/tieredrewards/keeper/msg_server.go (L347-386)
```go
func (ms msgServer) TriggerExitFromTier(ctx context.Context, msg *types.MsgTriggerExitFromTier) (*types.MsgTriggerExitFromTierResponse, error) {
	if err := msg.Validate(); err != nil {
		return nil, err
	}

	pos, err := ms.getPositionState(ctx, msg.PositionId)
	if err != nil {
		return nil, err
	}

	if err := ms.validateTriggerExit(pos.Position, msg.Owner); err != nil {
		return nil, err
	}

	tier, err := ms.getTier(ctx, pos.TierId)
	if err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	pos.TriggerExit(sdkCtx.BlockTime(), tier.ExitDuration)

	if err := ms.setPosition(ctx, pos.Position, nil); err != nil {
		return nil, err
	}

	if err := sdkCtx.EventManager().EmitTypedEvent(&types.EventExitTriggered{
		PositionId:   pos.Id,
		TierId:       pos.TierId,
		Owner:        pos.Owner,
		ExitUnlockAt: pos.ExitUnlockAt,
	}); err != nil {
		return nil, err
	}

	return &types.MsgTriggerExitFromTierResponse{
		ExitUnlockAt: pos.ExitUnlockAt,
		PositionId:   pos.Id,
	}, nil
}
```

**File:** x/tieredrewards/keeper/msg_validate.go (L221-228)
```go
	if !pos.HasTriggeredExit() {
		return types.ErrExitNotTriggered
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	if !pos.CompletedExitLockDuration(sdkCtx.BlockTime()) {
		return types.ErrExitLockDurationNotReached
	}
```
