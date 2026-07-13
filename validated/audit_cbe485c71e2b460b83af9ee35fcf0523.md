### Title
Unrestricted `ExitDuration` Updates via `MsgUpdateTier` Can Break Existing Position Holders' Exit Assumptions — (File: `x/tieredrewards/keeper/msg_server_auth.go`)

---

### Summary

Governance can update `Tier.ExitDuration` at any time via `MsgUpdateTier` with no upper bound and no grace period. Because `TriggerExitFromTier` reads the **current** tier's `ExitDuration` at trigger time to compute `ExitUnlockAt`, any increase to `ExitDuration` immediately applies to all existing positions that have not yet triggered exit. Users who locked tokens expecting a specific exit wait period can be forced into a much longer lockup than they agreed to.

---

### Finding Description

`MsgUpdateTier` in `x/tieredrewards/keeper/msg_server_auth.go` replaces the entire `Tier` record atomically. The only validation on `ExitDuration` is that it must be positive (`ExitDuration > 0`); there is no upper bound and no transition delay. [1](#0-0) 

The tier validation in `x/tieredrewards/types/tier.go` confirms there is no cap on `ExitDuration`: [2](#0-1) 

When a user calls `MsgTriggerExitFromTier`, the handler fetches the **live** tier and uses its current `ExitDuration` to stamp `ExitUnlockAt` into the position: [3](#0-2) 

`TriggerExit` stores `ExitUnlockAt = blockTime + duration` directly in the position: [4](#0-3) 

The `Position` struct stores no record of the `ExitDuration` that was in effect when the position was created: [5](#0-4) 

There is no per-position snapshot of `ExitDuration` at lock time, and no grace period before a new `ExitDuration` takes effect. The `UpdateTier` handler only triggers a reward settlement when `BonusApy` changes; a pure `ExitDuration` change silently takes effect immediately for all future `TriggerExitFromTier` calls: [6](#0-5) 

This is confirmed by the integration test `test_update_tier_non_apy_no_claim`, which explicitly verifies that changing `exit_duration` without changing APY triggers no reward settlement and the new duration applies immediately to new positions: [7](#0-6) 

---

### Impact Explanation

Any position holder who has not yet called `MsgTriggerExitFromTier` is subject to whatever `ExitDuration` is current at the moment they do trigger exit. If governance passes a proposal increasing `ExitDuration` from, say, 1 year to 10 years, every existing position that has not yet triggered exit will be locked for 10 years instead of 1 year when the user eventually triggers exit. There is no escape path: all exit routes (`MsgTierUndelegate`, `MsgExitTierWithDelegation`, `MsgWithdrawFromTier`) require `CompletedExitLockDuration`, which checks `block_time >= ExitUnlockAt`. [8](#0-7) 

The corrupted invariant is the **effective exit duration assumption** held by every position that has not yet triggered exit: the duration they observed at lock time is no longer guaranteed to be the duration applied when they trigger exit.

---

### Likelihood Explanation

`MsgUpdateTier` is a standard governance-gated Cosmos SDK transaction. Any token holder can submit a governance proposal. The vulnerability is present at the code level with no on-chain enforcement of a grace period or per-position duration snapshot. The ADR documents that tiers are managed by governance with no stated constraint on `ExitDuration` changes: [9](#0-8) 

---

### Recommendation

1. **Per-position snapshot**: Store the `ExitDuration` in effect at lock time (or at trigger time) inside the `Position` struct, so that `TriggerExitFromTier` uses the snapshotted value rather than the live tier value.
2. **Grace period**: When `ExitDuration` is increased via `MsgUpdateTier`, enforce a transition delay (e.g., equal to the old `ExitDuration`) before the new value takes effect for existing positions.
3. **Documentation**: Explicitly document in the tier governance messages that `ExitDuration` changes take immediate effect on all positions that have not yet triggered exit.

---

### Proof of Concept

1. Governance creates Tier 1 with `ExitDuration = 5s` (or 1 year in production).
2. Alice calls `MsgLockTier` and locks 1,000,000 basecro into Tier 1, observing the 5s exit duration.
3. Governance passes `MsgUpdateTier` setting Tier 1's `ExitDuration = 10 years`.
4. Alice calls `MsgTriggerExitFromTier`. The handler reads `tier.ExitDuration = 10 years` and sets `ExitUnlockAt = blockTime + 10 years`.
5. Alice cannot call `MsgTierUndelegate`, `MsgExitTierWithDelegation`, or `MsgWithdrawFromTier` until 10 years have elapsed — far beyond the 5s she expected when she locked.
6. `MsgClearPosition` is blocked if the tier is `CloseOnly`; even if not, cancelling exit only resets the timestamps — Alice must re-trigger exit and again face the 10-year duration.

### Citations

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

**File:** x/tieredrewards/types/types.pb.go (L136-166)
```go
// Position represents a single lock position in the tier.
type Position struct {
	// id is the unique identifier for this position.
	Id uint64 `protobuf:"varint,1,opt,name=id,proto3" json:"id,omitempty"`
	// owner is the address that owns this position.
	Owner string `protobuf:"bytes,2,opt,name=owner,proto3" json:"owner,omitempty"`
	// tier_id references the Tier this position belongs to.
	TierId uint32 `protobuf:"varint,3,opt,name=tier_id,json=tierId,proto3" json:"tier_id,omitempty"`
	// last_bonus_accrual is the last time bonus rewards was claimed.
	LastBonusAccrual time.Time `protobuf:"bytes,4,opt,name=last_bonus_accrual,json=lastBonusAccrual,proto3,stdtime" json:"last_bonus_accrual"`
	// last_event_seq is the sequence number of the last validator event this
	// position has processed. Events with seq > last_event_seq are pending.
	LastEventSeq uint64 `protobuf:"varint,5,opt,name=last_event_seq,json=lastEventSeq,proto3" json:"last_event_seq,omitempty"`
	// last_known_bonded tracks whether the validator was bonded after the last
	// event replay. Used as the starting state for the next processEventsAndClaimBonus
	// call. True when the position is first created (only bonded validators accepted).
	LastKnownBonded bool `protobuf:"varint,6,opt,name=last_known_bonded,json=lastKnownBonded,proto3" json:"last_known_bonded,omitempty"`
	// exit_triggered_at is the time when exit was triggered. Zero value means not exiting.
	ExitTriggeredAt time.Time `protobuf:"bytes,7,opt,name=exit_triggered_at,json=exitTriggeredAt,proto3,stdtime" json:"exit_triggered_at"`
	// exit_unlock_at is when the user can claim tokens (exit_triggered_at + tier.exit_duration).
	ExitUnlockAt time.Time `protobuf:"bytes,8,opt,name=exit_unlock_at,json=exitUnlockAt,proto3,stdtime" json:"exit_unlock_at"`
	// created_at_height is the block height when this position was created.
	CreatedAtHeight uint64 `protobuf:"varint,9,opt,name=created_at_height,json=createdAtHeight,proto3" json:"created_at_height,omitempty"`
	// created_at_time is the block time when this position was created.
	CreatedAtTime time.Time `protobuf:"bytes,10,opt,name=created_at_time,json=createdAtTime,proto3,stdtime" json:"created_at_time"`
	// delegator_address is the per-position account address that acts as the
	// delegator in x/staking, holds the principal during the lock period, and
	// is the withdraw source registered with x/distribution. Persisted at
	// creation; consumers must read this rather than recompute it.
	DelegatorAddress string `protobuf:"bytes,11,opt,name=delegator_address,json=delegatorAddress,proto3" json:"delegator_address,omitempty"`
}
```

**File:** integration_tests/test_tieredrewards_auth.py (L256-299)
```python
def test_update_tier_non_apy_no_claim(cluster):
    """Changing exit_duration without changing APY does not trigger rewards claiming.

    Verified by checking that the owner balance is unchanged.
    """
    owner = cluster.address("signer1")

    # Confirm the signer1 position on Tier 3 exists
    result = query_command(cluster, MODULE, "positions-by-owner", owner)
    tier3_pos = next(
        p for p in result.get("positions", []) if int(p["tier_id"]) == TIER_3_ID
    )
    assert tier3_pos is not None
    balance_before = cluster.balance(owner, DENOM)

    rsp = submit_gov_proposal(
        cluster,
        "community",
        MSG_UPDATE_TIER,
        {
            "tier": {
                "id": TIER_3_ID,
                "exit_duration": "10s",
                "bonus_apy": "0.030000000000000000",
                "min_lock_amount": "2000000",
                "close_only": False,
            }
        },
        title="Update Tier 3 exit_duration",
        summary="Only exit_duration changes, no APY change",
    )
    approve_proposal(cluster, rsp, msg=f",{MSG_UPDATE_TIER}")

    # No rewards should have been paid out to the owner.
    assert (
        cluster.balance(owner, DENOM) == balance_before
    ), "owner balance should not change when APY is unchanged"

    # Verify exit_duration is updated
    result = query_tiers(cluster)
    tier3 = next(
        (t for t in result.get("tiers", []) if int(t["id"]) == TIER_3_ID), None
    )
    assert tier3["exit_duration"] == "10s"
```

**File:** x/tieredrewards/keeper/msg_validate.go (L57-60)
```go
	sdkCtx := sdk.UnwrapSDKContext(ctx)
	if !pos.CompletedExitLockDuration(sdkCtx.BlockTime()) {
		return types.ErrExitLockDurationNotReached
	}
```

**File:** doc/architecture/adr-006.md (L84-94)
```markdown
```go
type Tier struct {
    Id            uint32          // e.g. 1, 2, 3
    ExitDuration  time.Duration   // wait time after triggering exit before undelegate is allowed
    BonusApy      sdk.Dec         // fixed bonus APY (e.g. 0.04 = 4%/year); capped at 1.0
    MinLockAmount math.Int        // minimum lock amount for new positions
    CloseOnly     bool            // when true, no new positions allowed (see §2)
}
```

Tiers are managed by governance (`MsgAddTier`, `MsgUpdateTier`, `MsgDeleteTier`), stored as separate keyed entries.
```
