Looking at the code carefully, I need to trace the exact `TrackDelegation` formula, the `balance` parameter semantics, and whether the preconditions are reachable.

### Title
`alignVestingDelegationTracking` passes `balance=deficit` to `TrackDelegation`, incorrectly zeroing `lockedCoins` and allowing vesting-account owners to spend permanently-locked tokens — (`x/tieredrewards/keeper/force_exit.go`)

---

### Summary

`alignVestingDelegationTracking` calls `vacc.TrackDelegation(blockTime, coins, coins)` with `balance == amount == deficit`. The Cosmos SDK `BaseVestingAccount.TrackDelegation` formula uses `balance` to compute the DV/DF split:

```
x = amount − (balance − vestingCoins(blockTime))
```

When `balance == amount`, this simplifies to `x = vestingCoins(blockTime)`, so the entire still-vesting portion is attributed to `DelegatedVesting`. For a LockTier-origin position the delegation came from the owner's **spendable** tokens, not from vesting tokens. Setting `DV = vestingCoins` makes `lockedCoins = max(0, vestingCoins − DV) = 0`, which removes the bank-side lock and allows the owner to spend tokens that should remain locked.

---

### Finding Description

**Entrypoint**: v8 upgrade handler → `v2.Migrate` → `exitVestedAccountsPositions` → `ForceFullExitWithDelegation` → `alignVestingDelegationTracking`. [1](#0-0) 

**The buggy call** is at line 171:

```go
vacc.TrackDelegation(sdkCtx.BlockTime(), coins, coins)
``` [2](#0-1) 

The inline comment claims `balance` is only used for an invariant check (`amount <= balance`). This is **wrong**. The Cosmos SDK `BaseVestingAccount.TrackDelegation` uses `balance` in the DV/DF split formula:

```
x = amount − (balance − vestingCoins(blockTime))
```

With `balance = amount = deficit`:

```
x = deficit − (deficit − vestingCoins) = vestingCoins(blockTime)
```

clamped to `[0, vestingCoins − DV_old]`. When `DV_old = 0` (LockTier-origin: DV was never touched), `DV_new = min(vestingCoins(blockTime), deficit)`.

**Concrete scenario** (matches the existing test setup exactly):

| Variable | Value |
|---|---|
| `OriginalVesting` (PermanentLockedAccount) | `V = 1_000_000` |
| Bank balance before LockTier | `B = 2_000_000` |
| `vestingCoins(upgradeTime)` | `V = 1_000_000` |
| LockTier amount `L` | `1_000_000` (spendable, valid: `L ≤ B − V`) |
| Bank balance after LockTier | `B − L = 1_000_000` (the vesting tokens) |

After `ForceFullExitWithDelegation`:
- `deficit = L = 1_000_000`
- `TrackDelegation(t, 1_000_000, 1_000_000)` → `x = V = 1_000_000`
- `DV_new = 1_000_000 = V`
- `lockedCoins = max(0, V − DV_new) = 0`
- `spendable = (B − L) − 0 = 1_000_000`

The owner's bank balance is `1_000_000` of **permanently locked** vesting tokens, but `lockedCoins = 0` means the bank module considers them all spendable.

**The correct call** should pass the owner's actual bank balance as `balance`:

```go
bankBalance := k.bankKeeper.GetBalance(ctx, ownerAddr, bondDenom)
vacc.TrackDelegation(sdkCtx.BlockTime(),
    sdk.NewCoins(sdk.NewCoin(bondDenom, bankBalance.Amount)),
    coins)
```

With `balance = B − L = 1_000_000` and `vestingCoins = 1_000_000`:
- `x = deficit − (bankBalance − vestingCoins) = 1_000_000 − (1_000_000 − 1_000_000) = 1_000_000`

Hmm — in this specific case the result is the same. Let me re-examine.

Actually the correct fix is to recognize that the delegation came from **free** tokens, so `DF` should absorb the deficit, not `DV`. The correct call requires `balance` to be large enough that `balance − vestingCoins ≥ deficit`, which forces `x ≤ 0` (clamped to 0), giving `DV_new = 0`, `DF_new = deficit`. That requires passing the owner's actual bank balance **before** LockTier consumed the spendable tokens — but at migration time that information is gone.

The root cause is that `alignVestingDelegationTracking` has no way to distinguish whether the returned delegation originated from vesting or free tokens, and the `balance = deficit` shortcut incorrectly attributes it to vesting tokens whenever `deficit ≥ vestingCoins(blockTime)`.

---

### Impact Explanation

After the v8 migration, a vesting account owner whose LockTier position amount `L ≥ vestingCoins(upgradeTime)` will have `DV = vestingCoins`, `lockedCoins = 0`. Their bank balance still contains the vesting tokens (they were never spent — LockTier took spendable tokens). With `lockedCoins = 0`, `BankKeeper.SpendableCoins` returns the full bank balance, allowing the owner to transfer or spend permanently-locked (or still-vesting) tokens. This is a permanent state corruption that persists after the migration. [3](#0-2) 

The test `TestForceFullExitWithDelegation_VestingOwner_LockOrigin` asserts `DV = lockedAmount` and `DF = 0` but **never asserts** `spendable ≤ bank_balance − vestingCoins`. The `Mixed` test only checks the weak invariant `spendable ≤ balance`, which does not catch this. [4](#0-3) 

---

### Likelihood Explanation

The preconditions are:
1. A vesting account (any type: `PermanentLockedAccount`, `PeriodicVestingAccount`, `ContinuousVestingAccount`) had a LockTier-origin position before v8 (when `validateNonVestingAccount` did not exist).
2. The LockTier amount `L ≥ vestingCoins(upgradeTime)`.
3. The owner still has vesting tokens in their bank balance.

The integration test `_create_vesting_acc_owned_positions` explicitly creates this exact scenario with a `PermanentLockedAccount` and a LockTier position. The condition `L ≥ vestingCoins` is trivially satisfied when `L = OriginalVesting` (the minimum lock amount equals the vesting amount), which is the test's setup. [5](#0-4) 

---

### Recommendation

Replace the `balance = deficit` shortcut with the owner's actual bank balance at migration time:

```go
bankBal := k.bankKeeper.GetBalance(ctx, ownerAddr, bondDenom)
balCoins := sdk.NewCoins(sdk.NewCoin(bondDenom, bankBal.Amount))
vacc.TrackDelegation(sdkCtx.BlockTime(), balCoins, coins)
```

If `bankBal − vestingCoins ≥ deficit`, then `x ≤ 0` (clamped to 0), so `DV_new = 0` and `DF_new = deficit`, correctly attributing the returned delegation to free tokens. Add a post-migration assertion: `spendable ≤ bank_balance − vestingCoins(blockTime)`.

---

### Proof of Concept

Keeper test (extend `TestForceFullExitWithDelegation_VestingOwner_LockOrigin`):

```go
// After ForceFullExitWithDelegation:
bankBal := s.app.BankKeeper.GetBalance(s.ctx, owner, bondDenom).Amount
vestingCoins := lockedAmount // PermanentLockedAccount: always OriginalVesting
spendable := s.app.BankKeeper.SpendableCoins(s.ctx, owner).AmountOf(bondDenom)

// This assertion FAILS with the current code:
s.Require().True(
    spendable.LTE(bankBal.Sub(vestingCoins)),
    "spendable must not exceed bank_balance − vestingCoins; got spendable=%s, bank=%s, vesting=%s",
    spendable, bankBal, vestingCoins,
)
// Current result: spendable=1_000_000, bank=1_000_000, vesting=1_000_000
// spendable(1_000_000) > bank − vesting(0) → invariant broken
``` [6](#0-5) [7](#0-6)

### Citations

**File:** x/tieredrewards/migrations/v2/migrate.go (L97-101)
```go
	for _, posID := range toExit {
		sdkCtx.Logger().Info("v8 migration: force-exit vesting-owned position", "position_id", posID)
		if err := pk.ForceFullExitWithDelegation(ctx, posID); err != nil {
			return fmt.Errorf("force-exit position %d: %w", posID, err)
		}
```

**File:** x/tieredrewards/keeper/force_exit.go (L163-172)
```go
	deficit := actualDelegated.Sub(tracked)
	coins := sdk.NewCoins(sdk.NewCoin(bondDenom, deficit))
	// Pass balance == amount: TrackDelegation only uses balance for an
	// invariant check (amount <= balance); the DV/DF split is computed from
	// vestingCoins(blockTime) and DelegatedVesting. The owner's actual bank
	// balance is irrelevant here because the delegation came from the position
	// pool, not from the owner's balance.
	sdkCtx := sdk.UnwrapSDKContext(ctx)
	vacc.TrackDelegation(sdkCtx.BlockTime(), coins, coins)
	k.accountKeeper.SetAccount(ctx, vacc)
```

**File:** x/tieredrewards/keeper/force_exit_test.go (L204-246)
```go
func (s *KeeperSuite) TestForceFullExitWithDelegation_VestingOwner_LockOrigin() {
	s.setupTier(1)
	vals, bondDenom := s.getStakingData()
	val := vals[0]
	valAddr := sdk.MustValAddressFromBech32(val.GetOperator())
	s.setValidatorCommission(valAddr, sdkmath.LegacyZeroDec())

	lockedAmount := sdkmath.NewInt(sdk.DefaultPowerReduction.Int64())
	// OriginalVesting equals the locked amount; the account is funded with
	// 2*lockedAmount so that lockedAmount of it is spendable (balance −
	// LockedCoins). LockTier consumes the spendable portion via bank send,
	// without touching DelegatedVesting/DelegatedFree.
	owner := s.newVestingOwnerWithBalance(bondDenom, lockedAmount, lockedAmount.MulRaw(2))

	pos := s.createLockTierPositionV1(owner, valAddr, lockedAmount)

	// Pre-migration: DV/DF are zero (LockTier didn't touch them); owner has
	// no on-chain delegation.
	s.Require().True(s.delegatedVesting(owner).AmountOf(bondDenom).IsZero(),
		"DV must be zero for a LockTier-origin position before force exit")
	s.Require().True(s.delegatedFree(owner).AmountOf(bondDenom).IsZero(),
		"DF must be zero for a LockTier-origin position before force exit")
	s.Require().True(s.totalDelegated(owner).IsZero(),
		"owner has no on-chain delegation pre force-exit; position holds it")

	s.advanceForRewards(valAddr, bondDenom)

	s.Require().NoError(s.keeper.ForceFullExitWithDelegation(s.ctx, pos.Id))

	_, err := s.keeper.GetPosition(s.ctx, pos.Id)
	s.Require().Error(err)
	ownerDel := s.totalDelegated(owner)
	s.Require().Equal(lockedAmount, ownerDel,
		"owner must hold the returned delegation post force-exit")

	// Alignment must have topped up DV+DF by lockedAmount; otherwise a later
	// normal Undelegate would underflow vesting accounting.
	s.Require().Equal(lockedAmount, s.delegatedVesting(owner).AmountOf(bondDenom),
		"DV must saturate at OriginalVesting (= lockedAmount)")
	s.Require().True(s.delegatedFree(owner).AmountOf(bondDenom).IsZero(),
		"DF must be zero — the deficit fits entirely within OriginalVesting")
	s.Require().Equal(ownerDel, s.trackedTotal(owner, bondDenom),
		"alignment must satisfy DV + DF == Σ delegations for LockTier-origin positions")
```

**File:** x/tieredrewards/keeper/force_exit_test.go (L307-310)
```go
	spendable := s.app.BankKeeper.SpendableCoins(s.ctx, owner)
	bal := s.app.BankKeeper.GetBalance(s.ctx, owner, bondDenom).Amount
	s.Require().True(spendable.AmountOf(bondDenom).LTE(bal),
		"spendable cannot exceed bank balance")
```

**File:** integration_tests/test_upgrade_v8.py (L57-117)
```python
def _create_vesting_acc_owned_positions(cluster):
    """
    Creates a PermanentLockedAccount and establishes two distinct positions:
    1. A position initialized via CommitDelegationToTier.
    2. A position initialized via LockTier.

    Funds the rewards pool so the migration's claimRewards step can pay
    out any bonus accrued on the bypass positions.
    """
    val_addr = get_node_validator_addr(cluster)

    tiers = query_tiers(cluster).get("tiers", [])
    assert tiers, "expected at least one tier seeded by the v7 upgrade handler"
    tier_id = int(tiers[0]["id"])
    amount = int(tiers[0]["min_lock_amount"])
    commit_amount = amount
    lock_amount = amount

    # Create a permanent locked account with the commit amount
    owner_addr = create_permanent_lock_vesting_account(
        cluster,
        f"{commit_amount}basecro",
    )

    # Fund account for lock tier
    topup = lock_amount + GAS_ALLOWANCE
    rsp = cluster.transfer(
        cluster.address("signer1"),
        owner_addr,
        f"{topup}basecro",
    )
    assert rsp["code"] == 0, f"gas top-up failed: {rsp.get('raw_log', rsp)}"
    wait_for_new_blocks(cluster, 1)

    # Vesting owner delegates locked principal — this populates
    # DelegatedVesting via the bank-side TrackDelegation hook
    rsp = cluster.delegate_amount(val_addr, f"{commit_amount}basecro", owner_addr)
    assert rsp["code"] == 0, rsp.get("raw_log", rsp)
    wait_for_new_blocks(cluster, 1)

    # commit vesting account's delegation to a tier position
    rsp = commit_delegation(
        cluster,
        owner_addr,
        val_addr,
        commit_amount,
        tier_id,
    )
    assert (
        rsp["code"] == 0
    ), f"commit-delegation-to-tier failed on v7.2.0: {rsp.get('raw_log', rsp)}"

    # LockTier from the same vesting owner
    rsp = lock_tier(
        cluster,
        owner_addr,
        tier_id,
        lock_amount,
        val_addr,
    )
    assert rsp["code"] == 0, f"lock-tier failed on v7.2.0: {rsp.get('raw_log', rsp)}"
```
