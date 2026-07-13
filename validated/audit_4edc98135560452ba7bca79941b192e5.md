### Title
Depleted Rewards Pool Blocks `MsgTierUndelegate` and `MsgExitTierWithDelegation`, Trapping User Locked Funds — (File: `x/tieredrewards/keeper/claim_rewards.go`)

---

### Summary

The `x/tieredrewards` module atomically settles bonus rewards **before** executing any position-exit operation. If the rewards pool holds less than the accrued bonus for a position, the settlement fails and the entire exit transaction reverts. A user who has already locked tokens in a tier and completed the exit commitment period is then unable to undelegate or transfer their delegation back until an admin refunds the pool — a direct analog to the paused-callback / manual-refund pattern in the external report.

---

### Finding Description

The ADR documents two invariants that combine to create the vulnerability:

**Invariant 1 — Reward settlement is mandatory before any exit mutation:**

> "MsgTierUndelegate, MsgTierRedelegate, MsgAddToTierPosition, MsgClearPosition, and MsgExitTierWithDelegation all claim rewards before modifying the position." [1](#0-0) 

**Invariant 2 — Bonus claim fails atomically when pool balance is insufficient:**

> "If pool balance < bonus: fail atomically (user retries later)" [2](#0-1) 

The two invariants compose as follows:

1. A user calls `MsgTierUndelegate` (or `MsgExitTierWithDelegation`) after their exit commitment has elapsed.
2. The handler internally calls the claim-rewards logic for the position.
3. If the rewards pool (`tieredrewards` module account) holds fewer tokens than the accrued bonus, the claim returns an error.
4. Because the claim is atomic with the exit mutation, the entire `MsgTierUndelegate` / `MsgExitTierWithDelegation` transaction reverts.
5. The user's locked tokens remain in the tier module's delegator sub-account; neither undelegation nor delegation-transfer is possible.
6. Recovery requires an admin (or governance) to fund the pool — manual intervention, not a user-initiated path.

The position lifecycle confirms that both primary exit paths share this dependency:

- **`MsgTierUndelegate`** — "Claims rewards first. Clears delegation state immediately." [3](#0-2) 
- **`MsgExitTierWithDelegation`** — "Claim rewards for position (settle base + bonus)" is the first step in the flow. [4](#0-3) 

The `SendCoinsFromModule` call that disburses bonus rewards is the concrete on-chain operation that fails when the pool is empty: [5](#0-4) 

The pool is a module account funded externally (e.g., via `bank.Send` to the module address): [6](#0-5) 

---

### Impact Explanation

A user who has:
- Locked tokens via `MsgLockTier` or `MsgCommitDelegationToTier`,
- Triggered exit via `MsgTriggerExitFromTier`, and
- Waited through the full `ExitDuration`,

…is entitled to withdraw their principal. However, if the rewards pool is depleted at the moment they attempt `MsgTierUndelegate` or `MsgExitTierWithDelegation`, both calls revert. The user's locked tokens remain delegated inside the tier module's per-position delegator address and cannot be recovered without admin action. The corrupted invariant is: **the user's principal balance in the tier module's delegator sub-account is inaccessible despite the exit commitment having fully elapsed**. [7](#0-6) 

---

### Likelihood Explanation

The pool is funded by external transfers and has no automatic top-up mechanism. Depletion is a realistic operational condition:

- High concurrent reward claims drain the pool faster than it is refunded.
- A governance delay in passing a pool-funding proposal leaves users blocked for the duration.
- An adversary who holds a large position can claim a large bonus, intentionally emptying the pool before other users attempt to exit.

The entry path is a standard, unprivileged `MsgTierUndelegate` or `MsgExitTierWithDelegation` transaction signed by the position owner — no special role required. [8](#0-7) 

---

### Recommendation

Decouple the bonus-reward settlement from the exit-mutation step for `MsgTierUndelegate` and `MsgExitTierWithDelegation`. Concretely:

- If the pool cannot cover the full bonus, disburse whatever is available and record the remainder as a claimable debt, **but still allow the undelegation / delegation-transfer to proceed**.
- Alternatively, allow users to explicitly opt out of bonus settlement during exit (forfeiting unclaimed bonus) so they can always recover their principal.
- The existing `force_exit.go` keeper file (`x/tieredrewards/keeper/force_exit.go`) may already contain a privileged bypass; if so, expose an equivalent user-accessible path that skips the pool check.



---

### Proof of Concept

```
1. Alice calls MsgLockTier(tier_id=1, amount=1_000_000basecro, validator=V)
   → tokens locked, position P created, delegation established.

2. Alice calls MsgTriggerExitFromTier(position_id=P)
   → ExitTriggeredAt = T0, ExitUnlockAt = T0 + ExitDuration (e.g. 365 days).

3. Time advances past ExitUnlockAt.

4. Meanwhile, other users drain the rewards pool to 0 via MsgClaimTierRewards.

5. Alice calls MsgTierUndelegate(owner=Alice, position_id=P).
   → Handler internally calls claimRewards(P).
   → claimRewards computes bonus > 0 (Alice has been bonded for 365 days).
   → SendCoinsFromModule(rewards_pool, Alice, bonus) fails: pool balance = 0.
   → MsgTierUndelegate reverts entirely.

6. Alice calls MsgExitTierWithDelegation(owner=Alice, position_id=P, amount=full).
   → Same internal claimRewards call → same failure.

7. Alice's 1_000_000basecro remain locked in the tier module's delegator
   sub-account. Recovery requires governance to fund the pool — manual
   intervention with no guaranteed timeline.
``` [9](#0-8) [10](#0-9)

### Citations

**File:** doc/architecture/adr-006.md (L36-42)
```markdown
        |     Transfer delegation back to owner on same validator.
        |     Supports partial exits. Position deleted if fully exited.
        |
        +---> Undelegate (starts staking unbonding)
              Wait for staking unbonding period (e.g. 21 days)
              Withdraw tokens -- position deleted
```
```

**File:** doc/architecture/adr-006.md (L161-166)
```markdown
| **MsgTierUndelegate** | Undelegate after exit commitment. Claims rewards first. Clears delegation state immediately. | Exit triggered; exit elapsed; delegated |
| **MsgTriggerExitFromTier** | Start exit commitment. | Not already exiting |
| **MsgClearPosition** | Cancel exit. Settles rewards first. If delegated, resets `LastBonusAccrual` to block_time. No-op if not exiting. | Tier not close-only; if exit elapsed: must be delegated and not unbonding |
| **MsgWithdrawFromTier** | Withdraw tokens + delete position. | Exit triggered; exit elapsed; not delegated; no pending unbonding |
| **MsgClaimTierRewards** | Claim base + bonus rewards for one or more positions. All positions must belong to the signer. | Owner match on all positions; position_ids non-empty, no duplicates, max 500; returns zero per position if not delegated |
| **MsgExitTierWithDelegation** | Transfer delegation back to owner (no unbonding). Supports partial exits. Deletes position if fully exited. | Exit triggered; exit elapsed; delegated; amount > 0; amount <= position amount; validator bonded; no active redelegation; partial exit: remaining >= MinLockAmount |
```

**File:** doc/architecture/adr-006.md (L196-225)
```markdown
### MsgClaimTierRewards Flow

```
-> Validate: owner match, position_ids non-empty, no duplicates
-> For each position: get position, validate ownership
-> Skip undelegated positions (return zero rewards for those)

-> For each delegated position:
     Phase 1 (Base — direct per-position withdraw):
     -> distribution.WithdrawDelegationRewards(delAddr, valAddr) -> rewards
     -> Rewards auto-route to the owner's bank balance via the WithdrawAddr
        configured at position creation. No module-side ratio math.
     -> Emit EventBaseRewardsClaimed; accumulate totalBase.

     Phase 2 (Bonus — lazy event replay):
     -> Walk pending validator events since pos.LastEventSeq
     -> For each bonded segment [segmentStart, eventTime):
        bonus += shares × event.TokensPerShare × apy × duration / SecondsPerYear
     -> Update bonded state based on event type (UNBOND→false, BOND→true, SLASH→unchanged)
     -> Decrement event reference count (garbage-collect if zero)
     -> Final segment: if bonded && val.IsBonded():
        bonus += shares × currentRate × apy × duration
     -> Advance LastBonusAccrual, LastEventSeq, LastKnownBonded
     -> If pool balance < bonus: fail atomically (user retries later)
     -> SendCoinsFromModule(rewards_pool, owner, bonus); emit EventBonusRewardsClaimed.

-> Persist each updated position
-> Emit aggregated EventTierRewardsClaimed with position_ids and totals
-> Return aggregated base_rewards, bonus_rewards, position_ids
```
```

**File:** doc/architecture/adr-006.md (L228-228)
```markdown
**Reward settlement before mutations:** MsgTierUndelegate, MsgTierRedelegate, MsgAddToTierPosition, MsgClearPosition, and MsgExitTierWithDelegation all claim rewards before modifying the position. This prevents double-counting and ensures bonus is capped at `ExitUnlockAt` before the cap is removed.
```

**File:** doc/architecture/adr-006.md (L236-236)
```markdown
-> Claim rewards for position (settle base + bonus)
```

**File:** integration_tests/tieredrewards_helpers.py (L140-144)
```python
def fund_pool(cluster, from_name, amount_coin):
    """Fund the rewards pool via a bank send to the module account."""
    from_addr = cluster.address(from_name)
    pool_addr = module_address(REWARDS_POOL_NAME)
    return cluster.transfer(from_addr, pool_addr, amount_coin)
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L1-1)
```go
package keeper
```
