### Title
No Governance Withdrawal Mechanism for Permanently Stuck Funds in `rewards_pool` Module Account — (`x/tieredrewards/keeper/abci.go`, `x/tieredrewards/types/keys.go`)

---

### Summary

The `x/tieredrewards` module maintains a `rewards_pool` module account that is explicitly allowed to receive external funds from any account. The pool is drained only through two automated code paths — both restricted to the bond denom. There is no governance message to withdraw or recover any remaining balance. If governance sets `TargetBaseRewardsRate = 0` and all positions exit, any residual bond-denom balance is permanently locked. Any non-bond-denom token ever sent to the pool is permanently locked regardless of module state.

---

### Finding Description

The `rewards_pool` module account is registered in `maccPerms` with `nil` permissions and is explicitly whitelisted to receive external `MsgSend` transfers: [1](#0-0) 

The pool is drained by exactly two code paths:

1. `BeginBlocker` → `topUpBaseRewards`, which reads only `bondDenom` balance and only when `TargetBaseRewardsRate > 0`: [2](#0-1) 

2. `processEventsAndClaimBonus` → `SendCoinsFromModule(rewards_pool, owner, bonus)`, which pays only bond-denom bonus rewards to position owners. [3](#0-2) 

The full set of governance-gated messages in the module is: [4](#0-3) 

None of these messages withdraw from `rewards_pool`. There is no `MsgWithdrawPool`, `MsgRecoverPool`, or equivalent. The module name and pool name are: [5](#0-4) 

---

### Impact Explanation

Two concrete stuck-fund scenarios exist:

**Scenario A — Bond denom stuck after module wind-down:**
Governance sets `TargetBaseRewardsRate = 0` (a legitimate operational decision to disable base-reward top-up, demonstrated in integration tests). All existing positions eventually exit via the normal lifecycle. Any remaining bond-denom balance in `rewards_pool` is permanently unrecoverable — BeginBlocker no longer drains it, and there are no positions left to claim bonus rewards.

**Scenario B — Non-bond denom permanently stuck:**
Because `rewards_pool` is in `moduleAccsAllowedToReceiveExternalFunds`, any account can send any denom to it via `MsgSend`. The BeginBlocker and bonus-claim paths only handle the bond denom. Any other denom sent to the pool is permanently locked with no code path to recover it.

The corrupted value is the balance of `rewards_pool` (address derivable from `sha256("rewards_pool")[:20]`).

---

### Likelihood Explanation

- Scenario A requires governance to set rate to 0 and all positions to exit — a realistic module-deprecation path. The integration test `test_zero_rate_no_topup` explicitly demonstrates governance setting rate to 0, confirming this is a supported operational state.
- Scenario B requires only a normal `MsgSend` to the pool address, which any account can execute at any time. Non-bond-denom tokens sent by mistake or by a misconfigured script are immediately and permanently lost.

---

### Recommendation

Add a governance-gated message (authority = `x/gov`) to withdraw an arbitrary amount of any denom from `rewards_pool` to a specified recipient, analogous to `MsgCommunityPoolSpend` in `x/distribution`. This allows the chain to recover pool funds if the module is deprecated or if non-bond-denom tokens are accidentally sent to the pool.

---

### Proof of Concept

1. Governance submits `MsgUpdateParams` setting `target_base_rewards_rate = "0"` — passes normally (demonstrated in `test_zero_rate_no_topup`).
2. All tier positions exit via the normal lifecycle (`MsgTriggerExitFromTier` → `MsgTierUndelegate` → `MsgWithdrawFromTier`).
3. Query `rewards_pool` balance: `chain-maind query bank balances <rewards_pool_addr>` — balance is non-zero.
4. Attempt any recovery: no governance message exists to move these funds. `MsgCommunityPoolSpend` targets only `x/distribution`. No module-account signing path exists.
5. For Scenario B: `chain-maind tx bank send <any_account> <rewards_pool_addr> 1000ibc/XXXX` — succeeds. Query balance shows IBC denom present. No code path in `abci.go` or `bonus_rewards.go` ever touches non-bond denoms. Funds are permanently stuck. [6](#0-5) [7](#0-6)

### Citations

**File:** app/app.go (L162-173)
```go
		tieredrewardstypes.RewardsPoolName: nil,
		// Legacy, not used anymore. Created by the previous implementation which uses this module to staking on behalf of users.
		tieredrewardstypes.ModuleName: {authtypes.Staking},
	}
	// moduleAccsAllowedToReceiveExternalFunds defines module accounts that can
	// receive tokens from external accounts via MsgSend, bypassing the default
	// block on sends to module accounts.
	moduleAccsAllowedToReceiveExternalFunds = map[string]bool{
		tieredrewardstypes.RewardsPoolName: true,
		// Legacy, not used anymore. Created by the previous implementation which uses this module to staking on behalf of users.
		tieredrewardstypes.ModuleName: true,
	}
```

**File:** x/tieredrewards/keeper/abci.go (L96-111)
```go
	poolAddr := k.accountKeeper.GetModuleAddress(types.RewardsPoolName)
	poolBalance := k.bankKeeper.GetBalance(ctx, poolAddr, bondDenom)
	topUpAmount := shortFallAmount
	if poolBalance.Amount.IsZero() {
		k.logger(ctx).Error("base rewards pool is empty, cannot top up validator rewards",
			"shortfall", shortFallAmount.String(),
		)
		return nil
	}
	if poolBalance.Amount.LT(shortFallAmount) {
		k.logger(ctx).Error("base rewards pool has insufficient funds, distributing remaining balance",
			"shortfall", shortFallAmount.String(),
			"pool_balance", poolBalance.Amount.String(),
		)
		topUpAmount = poolBalance.Amount
	}
```

**File:** x/tieredrewards/keeper/bonus_rewards.go (L1-5)
```go
package keeper

import (
	"context"
	"time"
```

**File:** doc/architecture/adr-006.md (L167-168)
```markdown
| **MsgUpdateParams** | Update module params. | Authority (gov) |
| **MsgAddTier** / **MsgUpdateTier** / **MsgDeleteTier** | Manage tiers. DeleteTier fails if positions exist. | Authority (gov) |
```

**File:** x/tieredrewards/types/keys.go (L20-27)
```go
const (
	ModuleName      = "tieredrewards"
	StoreKey        = ModuleName
	RewardsPoolName = "rewards_pool"

	// SecondsPerYear is 365.25 days, used to convert durations to years for bonus calculation.
	SecondsPerYear int64 = 31_557_600
)
```
