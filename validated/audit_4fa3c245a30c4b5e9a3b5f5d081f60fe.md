### Title
Duplicate Position IDs in Batched `ClaimTierRewards` Bypass Reward Checkpoint Enforcement — (`x/tieredrewards/keeper/msg_server.go`)

---

### Summary

`MsgClaimTierRewards` accepts a caller-supplied slice of position IDs and processes them as a batch. There is no deduplication guard on `msg.PositionIds`. Because all positions are loaded from storage into an in-memory slice **before** any checkpoint update is written back, submitting the same position ID twice causes the same pre-claim checkpoint state to be processed twice, enabling a user to double-claim bonus and base rewards from the tiered-rewards pool in a single transaction.

---

### Finding Description

`ClaimTierRewards` in `msg_server.go` collects position states in a loop and then delegates all reward settlement to `claimRewardsAndUpdatesPositions`:

```go
// x/tieredrewards/keeper/msg_server.go  lines 434-451
positions := make([]types.PositionState, 0, len(msg.PositionIds))
for _, posId := range msg.PositionIds {
    pos, err := ms.getPositionState(ctx, posId)   // reads from KV store
    ...
    positions = append(positions, pos)             // stale snapshot appended
}
totalBase, totalBonus, err := ms.claimRewardsAndUpdatesPositions(ctx, positions)
``` [1](#0-0) 

`getPositionState` reads the position and its delegation from the KV store at the moment of the call:

```go
// x/tieredrewards/keeper/position_state.go  lines 17-27
func (k Keeper) getPositionState(ctx context.Context, posId uint64) (types.PositionState, error) {
    pos, err := k.getPosition(ctx, posId)
    ...
    del, err := k.getDelegation(ctx, pos.DelegatorAddress)
    ...
    return types.PositionState{Position: pos, Delegation: del}, nil
}
``` [2](#0-1) 

The implicit constraint — that each position ID must appear **at most once** in a single `ClaimTierRewards` call — is never enforced. Neither `msg.Validate()` nor the loop body checks for duplicates. When `[posId, posId]` is supplied:

1. Both iterations of the loop read the **same pre-claim checkpoint** from storage and append two identical `PositionState` copies to `positions`.
2. `claimRewardsAndUpdatesPositions` processes the slice sequentially. The first entry triggers reward calculation from checkpoint `C₀` → `C_now`, writes the updated checkpoint back to storage, and transfers rewards to the owner.
3. The second entry still carries the **stale** `C₀` checkpoint (it was snapshotted before step 2 ran). The function recalculates the same reward interval `C₀` → `C_now` and transfers the same reward amount a second time.

The same position's bonus checkpoints are consumed twice, draining the rewards pool by 2× the legitimate entitlement.

---

### Impact Explanation

The corrupted value is the **tiered-rewards pool balance** (base and bonus reward coins held by the module account) and the **position's bonus checkpoint sequence**. A user with a single valid position can multiply their reward claim by the number of times they repeat the position ID in one message. Because the module account funds are finite and shared across all participants, repeated exploitation depletes rewards owed to other delegators.

---

### Likelihood Explanation

Any delegator who holds at least one tiered position can craft this transaction directly via CLI or any IBC/authz-relayed message. No privileged role, leaked key, or social engineering is required. The entry path is a standard `MsgClaimTierRewards` transaction signed by the position owner.

---

### Recommendation

Add a deduplication check in `ClaimTierRewards` before the positions are loaded:

```go
seen := make(map[uint64]struct{}, len(msg.PositionIds))
for _, posId := range msg.PositionIds {
    if _, dup := seen[posId]; dup {
        return nil, errorsmod.Wrapf(types.ErrInvalidPositionID,
            "duplicate position id %d in claim batch", posId)
    }
    seen[posId] = struct{}{}
    ...
}
```

Alternatively, enforce uniqueness inside `msg.Validate()` so the constraint is checked at the message-validation layer before any keeper logic runs.

---

### Proof of Concept

1. User creates a tiered position and accrues rewards over several blocks. Position ID is `42`.
2. User submits `MsgClaimTierRewards{Owner: user, PositionIds: [42, 42]}`.
3. The loop in `ClaimTierRewards` calls `getPositionState(42)` twice, producing two identical `PositionState` snapshots with the same bonus checkpoint `C₀`.
4. `claimRewardsAndUpdatesPositions` processes entry 0: computes reward for `C₀→C_now`, sends coins to user, writes updated checkpoint `C_now` to storage.
5. `claimRewardsAndUpdatesPositions` processes entry 1: the in-memory snapshot still shows `C₀`; computes the same reward for `C₀→C_now` again, sends the same coins to user a second time.
6. User receives 2× the legitimate reward in a single transaction; the module reward pool is debited twice for one position's entitlement. [3](#0-2) [2](#0-1)

### Citations

**File:** x/tieredrewards/keeper/msg_server.go (L429-468)
```go
func (ms msgServer) ClaimTierRewards(ctx context.Context, msg *types.MsgClaimTierRewards) (*types.MsgClaimTierRewardsResponse, error) {
	if err := msg.Validate(); err != nil {
		return nil, err
	}

	positions := make([]types.PositionState, 0, len(msg.PositionIds))
	for _, posId := range msg.PositionIds {
		pos, err := ms.getPositionState(ctx, posId)
		if err != nil {
			return nil, err
		}

		if err := ms.validateClaimRewards(pos.Position, msg.Owner); err != nil {
			return nil, err
		}

		positions = append(positions, pos)
	}

	totalBase, totalBonus, err := ms.claimRewardsAndUpdatesPositions(ctx, positions)
	if err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	if err := sdkCtx.EventManager().EmitTypedEvent(&types.EventTierRewardsClaimed{
		Owner:        msg.Owner,
		PositionIds:  msg.PositionIds,
		BaseRewards:  totalBase,
		BonusRewards: totalBonus,
	}); err != nil {
		return nil, err
	}

	return &types.MsgClaimTierRewardsResponse{
		BaseRewards:  totalBase,
		BonusRewards: totalBonus,
		PositionIds:  msg.PositionIds,
	}, nil
}
```

**File:** x/tieredrewards/keeper/position_state.go (L17-27)
```go
func (k Keeper) getPositionState(ctx context.Context, posId uint64) (types.PositionState, error) {
	pos, err := k.getPosition(ctx, posId)
	if err != nil {
		return types.PositionState{}, err
	}
	del, err := k.getDelegation(ctx, pos.DelegatorAddress)
	if err != nil {
		return types.PositionState{}, err
	}
	return types.PositionState{Position: pos, Delegation: del}, nil
}
```
