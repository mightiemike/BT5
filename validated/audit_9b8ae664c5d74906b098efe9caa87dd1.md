Based on my investigation of the codebase, here is my assessment:

---

### Title
Permanently Locked Funds via MsgSend to Unblocked Legacy `tieredrewards` Module Account - (`app/app.go`)

### Summary

The legacy `tieredrewardstypes.ModuleName` ("tieredrewards") module account is explicitly whitelisted in `moduleAccsAllowedToReceiveExternalFunds`, making it reachable via `MsgSend`. The code comment acknowledges it is "Legacy, not used anymore." No current code path reads from or sends funds out of this module account, so any tokens sent there are permanently unrecoverable.

### Finding Description

In `app/app.go`, `BlockedAddrs()` computes blocked status as `!moduleAccsAllowedToReceiveExternalFunds[acc]`. Because `tieredrewardstypes.ModuleName` maps to `true` in that map, the bank keeper does **not** block sends to it: [1](#0-0) [2](#0-1) 

The module account has `{authtypes.Staking}` permission in `maccPerms` (a legacy remnant), but the current tieredrewards implementation never uses the `ModuleName` account for any fund flow: [3](#0-2) 

All active fund flows in the current implementation use only `types.RewardsPoolName`:
- `BeginBlocker` drains from `RewardsPoolName` → `distribution` [4](#0-3) 
- Bonus rewards pay from `RewardsPoolName` → owner [5](#0-4) 
- `WithdrawFromTier` sends from `pos.DelegatorAddress` (per-position derived address) → owner, never touching `ModuleName` [6](#0-5) 
- `LockTier` sends owner → per-position `delAddr`, not to `ModuleName` [7](#0-6) 

The `tieredrewardstypes.ModuleName` constant is `"tieredrewards"`: [8](#0-7) 

### Impact Explanation

Any user who sends `basecro` (or any denom) to `authtypes.NewModuleAddress("tieredrewards")` via `MsgSend` will succeed (the send is not blocked), and those funds will be permanently locked. There is no `MsgServer` handler, no governance message, no `BeginBlocker`/`EndBlocker` path, and no admin function that reads from or drains the `tieredrewardstypes.ModuleName` module account. The funds are irrecoverable without a chain upgrade.

### Likelihood Explanation

A user must deliberately send to the module account address. This is not triggered by normal usage. However, the address is derivable from the well-known module name `"tieredrewards"`, and the unblocked status means the send succeeds silently with no warning. The risk is low in practice but the loss is total and permanent when it occurs.

### Recommendation

Remove `tieredrewardstypes.ModuleName` from `moduleAccsAllowedToReceiveExternalFunds`. Since the module is acknowledged as "not used anymore," there is no legitimate reason for external accounts to send funds to it. Blocking it prevents accidental or intentional permanent fund loss. [9](#0-8) 

### Proof of Concept

```go
// 1. Derive the legacy module account address
legacyAddr := authtypes.NewModuleAddress(tieredrewardstypes.ModuleName)

// 2. MsgSend succeeds — address is not in BlockedAddrs
msg := banktypes.NewMsgSend(userAddr, legacyAddr, sdk.NewCoins(sdk.NewCoin("basecro", math.NewInt(1_000_000))))
// → tx succeeds, funds transferred

// 3. Attempt all known recovery paths — all fail:
// - MsgWithdrawFromTier: requires a position ID owned by sender, reads from pos.DelegatorAddress, not ModuleName
// - MsgExitTierWithDelegation: same — position-scoped
// - MsgClaimTierRewards: pays from RewardsPoolName, not ModuleName
// - bank.MsgSend FROM module account: blocked by SDK (module accounts cannot sign)
// - No governance message exists to drain ModuleName

// Result: funds permanently locked in tieredrewardstypes.ModuleName account
```

### Citations

**File:** app/app.go (L162-165)
```go
		tieredrewardstypes.RewardsPoolName: nil,
		// Legacy, not used anymore. Created by the previous implementation which uses this module to staking on behalf of users.
		tieredrewardstypes.ModuleName: {authtypes.Staking},
	}
```

**File:** app/app.go (L166-173)
```go
	// moduleAccsAllowedToReceiveExternalFunds defines module accounts that can
	// receive tokens from external accounts via MsgSend, bypassing the default
	// block on sends to module accounts.
	moduleAccsAllowedToReceiveExternalFunds = map[string]bool{
		tieredrewardstypes.RewardsPoolName: true,
		// Legacy, not used anymore. Created by the previous implementation which uses this module to staking on behalf of users.
		tieredrewardstypes.ModuleName: true,
	}
```

**File:** app/app.go (L855-861)
```go
func (app *ChainApp) BlockedAddrs() map[string]bool {
	blockedAddrs := make(map[string]bool)
	for acc := range maccPerms {
		blockedAddrs[authtypes.NewModuleAddress(acc).String()] = !moduleAccsAllowedToReceiveExternalFunds[acc]
	}

	return blockedAddrs
```

**File:** x/tieredrewards/keeper/abci.go (L96-113)
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

	err = k.bankKeeper.SendCoinsFromModuleToModule(ctx, types.RewardsPoolName, distributiontypes.ModuleName, sdk.NewCoins(sdk.NewCoin(bondDenom, topUpAmount)))
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L239-239)
```go
	if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.RewardsPoolName, ownerAddr, bonusCoins); err != nil {
```

**File:** x/tieredrewards/keeper/msg_server.go (L52-63)
```go
	delAddr, err := ms.createPositionDelegatorAccount(ctx, ownerAddr, id)
	if err != nil {
		return nil, err
	}

	if err := ms.lockFunds(ctx, ownerAddr, delAddr, msg.Amount); err != nil {
		return nil, err
	}

	if _, err := ms.delegate(ctx, delAddr, valAddr, msg.Amount); err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/msg_server.go (L494-499)
```go
	balances := ms.bankKeeper.SpendableCoins(ctx, delAddr)
	if !balances.IsZero() {
		if err := ms.bankKeeper.SendCoins(ctx, delAddr, ownerAddr, balances); err != nil {
			return nil, err
		}
	}
```

**File:** x/tieredrewards/types/keys.go (L21-23)
```go
	ModuleName      = "tieredrewards"
	StoreKey        = ModuleName
	RewardsPoolName = "rewards_pool"
```
