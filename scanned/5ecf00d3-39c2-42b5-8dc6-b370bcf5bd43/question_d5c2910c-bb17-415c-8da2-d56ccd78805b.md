[File: 'cmd/chain-maind/app/appwin.go -> Scope: Critical. Tiered rewards position, delegation, redelegation, slashing, exit, withdrawal, or reward-accounting flaw lets an attacker withdraw delegated stake, claim rewards, or move voting power not owned by them.'] [Function: validateNonVestingAccount / LockTier / MsgLockTier] Can an attacker create a tier position for an account that becomes a vesting account after position creation, given that validateNonVestingAccount is only called during validateNewPosition (position creation) and not during subsequent operations like AddToTierPosition or TierRedelegate, trigger the call sequence LockTier(owner=normalAccount) -> position created -> owner account is converted to vesting account (e.g., via a vesting account creation message) -> AddToTierPosition -> validateAddToPosition does not call validateNonVestingAccount -> lockFunds(ownerAddr, delAddr) -> bankKeeper.SendCoins from vesting account, violating the invariant that vesting accounts must not participate in tier positions (ErrVestingAccountNotAllowed), causing scoped impact: a vesting account bypasses the vesting restriction by first creating a position as a normal account, then having the account converted to vesting, allowing the vesting account to lock unvested tokens into a tier position and potentially withdraw them earlier than the vesting schedule allows? Proof idea: keeper test where an account is converted to vesting after LockTier; assert that

### Citations

**File:** cmd/chain-maind/app/appwin.go (L1-12)
```go
//go:build windows

package app

import (
