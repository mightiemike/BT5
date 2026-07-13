### Title
Tier `BonusApy` Update Overwrites Historical Accrual Rate for All Existing Positions — (File: `x/tieredrewards/keeper/tier.go`)

---

### Summary

The `x/tieredrewards` module uses a lazy bonus calculation model where positions store a `TierId` reference and a `LastBonusAccrual` timestamp, but never snapshot the `BonusApy` in effect at the time of accrual. When governance updates a tier's `BonusApy` via `SetTier`, the new rate is silently applied retroactively to the entire unclaimed accrual window of every existing position in that tier, producing incorrect bonus payouts — either over-minting from the bonus pool or under-paying delegators.

---

### Finding Description

**Root cause — `SetTier` updates the rate with no prior settlement:**

`SetTier` in `x/tieredrewards/keeper/tier.go` writes the new `Tier` record (including the updated `BonusApy`) directly to the store with no side-effects on existing positions: [1](#0-0) 

There is no call to settle pending bonuses for positions already in the tier, no APY-change event appended to the validator event pipeline, and no snapshot of the old rate stored anywhere.

**Positions carry only a tier reference, never a rate snapshot:**

The `Position` struct stores `TierId` (a live reference) but has no `BonusApy` field: [2](#0-1) 

The only time-tracking fields are `LastBonusAccrual` and `LastEventSeq`. When the lazy bonus calculation runs, it must look up the tier to obtain the APY — and at that point it reads the *current* (post-update) value, not the value that was in effect during the accrual window.

**Validator events track slashing, not APY changes:**

The event pipeline (`appendValidatorEvent`, `getValidatorEventsSince`) records only validator bonding/slashing events: [3](#0-2) 

There is no analogous mechanism to record an APY-change event. The lazy calculation therefore has no way to reconstruct the correct rate for the pre-update period.

**Lazy calculation applies a single rate to the entire window:**

The wiki confirms: *"The bonus is calculated lazily based on the APY of the tier and the duration the funds were locked."* Because the APY is fetched from the live tier record at claim time, the entire duration since `LastBonusAccrual` — including the period before the governance update — is priced at the new rate. [4](#0-3) 

---

### Impact Explanation

| Governance action | Effect on existing unclaimed positions |
|---|---|
| APY raised (e.g. 5% → 10%) | Bonus pool over-pays; unbacked CRO minted for the pre-update window |
| APY lowered (e.g. 10% → 5%) | Delegators under-paid; accrued entitlement silently erased |

The corrupted value is the **bonus CRO minted from the module pool** for every position in the affected tier whose `LastBonusAccrual` predates the governance update. With large positions or long unclaimed windows the discrepancy can be substantial.

---

### Likelihood Explanation

Any governance proposal that legitimately adjusts a tier's `BonusApy` — a routine economic parameter change — triggers this bug for every position in that tier that has not claimed since the update. No special attacker capability is required beyond submitting and passing a governance proposal, which is a standard, unprivileged Cosmos SDK transaction flow. The longer positions go without claiming (which is normal for long-lock tiers), the larger the mis-priced window.

---

### Recommendation

Before persisting the new `BonusApy` in `SetTier`, iterate over all positions in the tier using the `PositionsByTier` index and settle their pending bonus accrual at the *old* rate up to the current block time. Only after settlement should the new rate be written. This mirrors the mitigation recommended in H-06: *"calculate the interest before updating the APR when the lent amount is non-zero."* [1](#0-0) [5](#0-4) 

---

### Proof of Concept

1. Governance creates Tier T with `BonusApy = 5%`.
2. Alice locks 1,000,000 CRO into Tier T at block time `t0`. Her `LastBonusAccrual = t0`.
3. One year passes (`t1 = t0 + 1yr`). Alice does not claim. Correct accrued bonus = **50,000 CRO**.
4. Governance passes `MsgSetTier` updating Tier T's `BonusApy` to `10%`. `SetTier` writes the new tier record with no settlement of Alice's position.
5. Alice claims her bonus at `t1`. The lazy calculation fetches `BonusApy = 10%` from the live tier record and applies it to the full year window `[t0, t1]`.
6. Alice receives **100,000 CRO** — double her entitlement — draining an extra 50,000 CRO from the bonus pool.

The reverse scenario (APY decrease) silently destroys 50,000 CRO of Alice's earned entitlement with no recourse.

### Citations

**File:** x/tieredrewards/keeper/tier.go (L27-35)
```go
func (k Keeper) SetTier(ctx context.Context, tier types.Tier) error {
	if err := tier.Validate(); err != nil {
		return err
	}
	if err := k.Tiers.Set(ctx, tier.Id, tier); err != nil {
		return errorsmod.Wrapf(err, "%s (tier id %d)", types.ErrTierStore.Error(), tier.Id)
	}
	return nil
}
```

**File:** x/tieredrewards/types/position.go (L15-27)
```go
func NewPosition(id uint64, owner string, tierId uint32, delegatorAddress string, createdAtHeight, lastEventSeq uint64, lastBonusAccrual time.Time, lastKnownBonded bool, createdAtTime time.Time) Position {
	return Position{
		Id:               id,
		Owner:            owner,
		TierId:           tierId,
		DelegatorAddress: delegatorAddress,
		CreatedAtHeight:  createdAtHeight,
		CreatedAtTime:    createdAtTime,
		LastEventSeq:     lastEventSeq,
		LastBonusAccrual: lastBonusAccrual,
		LastKnownBonded:  lastKnownBonded,
	}
}
```

**File:** x/tieredrewards/types/position.go (L65-68)
```go
func (p *Position) UpdateBonusCheckpoints(lastEventSeq uint64, t time.Time, lastKnownBonded bool) {
	p.LastEventSeq = lastEventSeq
	p.LastBonusAccrual = t
	p.LastKnownBonded = lastKnownBonded
```

**File:** x/tieredrewards/keeper/validator_events.go (L23-34)
```go
func (k Keeper) appendValidatorEvent(ctx context.Context, valAddr sdk.ValAddress, event types.ValidatorEvent) (uint64, error) {
	seq, err := k.incrementValidatorEventSeq(ctx, valAddr)
	if err != nil {
		return 0, err
	}

	if err := k.ValidatorEvents.Set(ctx, collections.Join(valAddr, seq), event); err != nil {
		return 0, err
	}

	return seq, nil
}
```

**File:** x/tieredrewards/keeper/position.go (L279-282)
```go
func (k Keeper) getPositionsIdsByTier(ctx context.Context, tierId uint32) ([]uint64, error) {
	rng := collections.NewPrefixedPairRange[uint32, uint64](tierId)
	return collectPairKeySetK2(ctx, k.PositionsByTier, rng)
}
```
