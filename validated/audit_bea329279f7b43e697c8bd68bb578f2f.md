### Title
Stale `ExitUnlockAt` Snapshot Allows Bypassing Governance-Extended Exit Duration — (File: `x/tieredrewards/keeper/msg_server.go`)

### Summary

When a position triggers exit, `ExitUnlockAt` is computed once as `ExitTriggeredAt + tier.ExitDuration` and persisted on the `Position` record. If governance later increases `tier.ExitDuration` via `MsgUpdateTier`, all positions that have already triggered exit retain their old, shorter `ExitUnlockAt` values. The exit-execution handlers (`MsgExitTierWithDelegation`, `MsgTierUndelegate`) check only the stored snapshot, never the live tier parameter, so those positions can exit at the old (shorter) time — bypassing the new governance-mandated lock period.

### Finding Description

**Snapshot at trigger time.** `MsgTriggerExitFromTier` (and the `trigger_exit_immediately` path in `MsgLockTier`) sets two fields on the position:

```
ExitTriggeredAt = block_time
ExitUnlockAt    = block_time + tier.ExitDuration   // snapshot
```

`ExitUnlockAt` is stored directly on the `Position` proto and persisted to the KV store. [1](#0-0) 

**Exit-elapsed check uses the stored snapshot.** Both `MsgExitTierWithDelegation` and `MsgTierUndelegate` gate execution on `block_time >= pos.ExitUnlockAt`. They read the position from state and compare against the persisted value — they never re-fetch `tier.ExitDuration` to recompute the expected unlock time. [2](#0-1) 

**`MsgUpdateTier` settles `BonusApy` but ignores `ExitDuration`.** The `UpdateTier` handler has explicit logic to claim all positions in the tier before a `BonusApy` change takes effect, advancing `LastBonusAccrual` to the current block time so the old rate is correctly settled. No equivalent logic exists for `ExitDuration` changes: existing positions' `ExitUnlockAt` values are never recomputed or updated.
<cite repo="Oyahkilomeikhide/chain-main--015" path="x/tieredrewards/keeper/msg_

### Citations

**File:** proto/chainmain/tieredrewards/v1/types.proto (L67-72)
```text
  google.protobuf.Timestamp exit_triggered_at = 7
      [(gogoproto.nullable) = false, (gogoproto.stdtime) = true, (amino.dont_omitempty) = true];

  // exit_unlock_at is when the user can claim tokens (exit_triggered_at + tier.exit_duration).
  google.protobuf.Timestamp exit_unlock_at = 8
      [(gogoproto.nullable) = false, (gogoproto.stdtime) = true, (amino.dont_omitempty) = true];
```

**File:** doc/architecture/adr-006.md (L232-250)
```markdown
### MsgExitTierWithDelegation Flow

```
-> Validate: owner match, delegated, exit triggered, exit elapsed, amount > 0, amount <= position amount, no active redelegation
-> Claim rewards for position (settle base + bonus)
-> positionAmount = TokensFromShares(pos.Delegation.Shares)  // pre-transfer live value
-> If amount == positionAmount (full exit): unbondedShares = pos.Delegation.Shares
   Else (partial): unbondedShares = ValidateUnbondAmount(posDelAddr, valAddr, amount)
-> transferDelegationFromPosition: Unbond(posDelAddr, valAddr, unbondedShares) -> transferredAmount
   Re-fetch validator, Delegate(owner, transferredAmount, validator) — instant, no unbonding
-> If full exit:
     sweep the position's spendable bank balance (SpendableCoins, not GetAllBalances)
     from posDelAddr to owner.
     delete position (all indexes cleaned up, WithdrawAddr cleared via DeleteDelegatorWithdrawAddr)
   Else:
     remaining token value must meet tier.MinLockAmount (post-transfer check on actual amount)
     save position
-> Emit EventExitTierWithDelegation(position_id, tier_id, owner, validator, transferred_amount, transferred_shares, full_exit)
```
```
