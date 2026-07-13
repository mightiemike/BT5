### Title
Duplicate Position IDs in `ClaimTierRewards` Enable Double-Claim of Bonus Rewards — (File: `x/tieredrewards/keeper/msg_server.go`)

---

### Summary

`MsgClaimTierRewards` accepts a caller-supplied list of position IDs with no deduplication guard. When the same position ID appears twice in the list, the keeper loads two stale, identical copies of the position state before any store update occurs. The second iteration then re-processes the same validator events and issues a second `SendCoinsFromModuleToAccount` transfer from the `RewardsPoolName` module account to the owner, while also overwriting the position's `LastEventSeq` checkpoint with the stale value, enabling the same exploit in subsequent transactions.

---

### Finding Description

In `ClaimTierRewards` (`x/tieredrewards/keeper/msg_server.go`, lines 429–468), all positions are loaded from the store into a Go slice **before** any state mutation:

```go
positions := make([]types.PositionState, 0, len(msg.PositionIds))
for _, posId := range msg.PositionIds {
    pos, err := ms.getPositionState(ctx, posId)   // loaded from store
    ...
    positions = append(positions, pos)             // snapshot, not a live reference
}
totalBase, totalBonus, err := ms.claimRewardsAndUpdatesPositions(ctx, positions)
```

There is no uniqueness check on `msg.PositionIds`. If an attacker passes `[posId, posId]`, both slice entries hold the same pre-mutation state.

`claimRewardsAndUpdatesPositions` (`x/tieredrewards/keeper/claim_rewards.go`, lines 106–135) then iterates over the slice:

```go
for i := range positions {
    pos := &positions[i]
    base, err := k.claimBaseRewards(ctx, *pos)          // WithdrawDelegationRewards
    bonus, err := k.processEventsAndClaimBonus(ctx, pos) // pays from RewardsPoolName
    k.setPosition(ctx, pos.Position, nil)                // writes back to store
}
```

**First iteration (i=0):**
- `claimBaseRewards` drains the distribution-module rewards for the position's delegator address (idempotent; second call returns zero).
- `processEventsAndClaimBonus` walks validator events since `pos.LastEventSeq`, computes bonus, calls `decrementEventRefCount` for each event, and executes `bankKeeper.SendCoinsFromModuleToAccount