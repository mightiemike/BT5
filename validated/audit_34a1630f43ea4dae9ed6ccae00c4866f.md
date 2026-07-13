### Title
Permanently Irrecoverable Tokens Sent to Legacy `tieredrewards` Module Account via `MsgSend` — (File: app/app.go)

---

### Summary
The legacy `tieredrewards` module account (`tieredrewardstypes.ModuleName`) is explicitly whitelisted to receive external funds via `MsgSend`, bypassing the Cosmos SDK's default block on sends to module accounts. The code itself acknowledges this account is "Legacy, not used anymore." Because no active code path in the module ever reads from or drains this account, any tokens sent to it are permanently locked with no on-chain recovery mechanism.

---

### Finding Description
In `app/app.go`, two maps govern module-account access:

```go
maccPerms = map[string][]string{
    ...
    tieredrewardstypes.RewardsPoolName: nil,
    // Legacy, not used anymore. Created by the previous implementation which uses this module to staking on behalf of users.
    tieredrewardstypes.ModuleName: {authtypes.Staking},
}
moduleAccsAllowedToReceiveExternalFunds = map[string]bool{
    tieredrewardstypes.RewardsPoolName: true,
    // Legacy, not used anymore. Created by the previous implementation which uses this module to staking on behalf of users.
    tieredrewardstypes.ModuleName: true,
}
``` [1](#0-0) 

`BlockedAddrs()` derives the bank-level block list from this map:

```go
func (app *ChainApp) BlockedAddrs() map[string]bool {
    blockedAddrs := make(map[string]bool)
    for acc := range maccPerms {
        blockedAddrs[authtypes.NewModuleAddress(acc).String()] = !moduleAccsAllowedToReceiveExternalFunds[acc]
    }
    return blockedAddrs
}
``` [2](#0-1) 

Because `tieredrewardstypes.ModuleName` maps to `true` in `moduleAccsAllowedToReceiveExternalFunds`, the bank keeper will accept a `MsgSend` to the `tieredrewards` module account address from any unprivileged user. The module account address is deterministic: `sha256("tieredrewards")[:20]`.

The active rewards pool is `RewardsPoolName` ("rewards_pool"), not `ModuleName` ("tieredrewards"). The `tieredrewards` module account has no active keeper paths that spend, stake, or redistribute its balance. The `{authtypes.Staking}` permission registered for it is vestigial — no current message handler calls `bankKeeper.SendCoinsFromModuleToAccount` or any equivalent drain on this account. [3](#0-2) 

---

### Impact Explanation
Any user who sends tokens to the `tieredrewards` module account address (e.g., by querying the module account address via CLI or following outdated documentation that referenced the old implementation) will permanently lose those tokens. There is no governance proposal type, no admin message, and no keeper function that can drain the `tieredrewards` module account back to users. The corrupted value is the sender's bank balance: tokens are transferred into the module account and cannot be recovered.

---

### Likelihood Explanation
The `tieredrewards` module account address is publicly derivable from the module name. Users who intend to fund the rewards pool (`rewards_pool`) may confuse the two module account addresses, especially given that both are whitelisted to receive external funds and both belong to the same module. Old scripts, documentation, or integrations from the previous implementation that referenced the `tieredrewards` module account for funding would direct tokens to the irrecoverable address. The likelihood is low-medium: it requires a user to send to the module account address, but the explicit whitelist makes this a reachable, non-rejected path.

---

### Recommendation
Remove `tieredrewardstypes.ModuleName` from `moduleAccsAllowedToReceiveExternalFunds` in `app/app.go`. Since the module no longer uses this account, there is no legitimate reason for external accounts to send tokens to it. Blocking sends to it (the Cosmos SDK default for module accounts) will prevent tokens from being permanently locked there. If backward compatibility requires keeping the account registered in `maccPerms`, the whitelist entry should still be removed.

---

### Proof of Concept
1. Derive the `tieredrewards` module account address:
   ```
   addr = bech32("cro", sha256("tieredrewards")[:20])
   ```
2. Submit a `MsgSend` from any user account to this address with any coin amount.
3. The bank keeper accepts the send because `BlockedAddrs()` returns `false` for this address.
4. Query the module account balance — tokens are present.
5. Attempt any on-chain recovery: no message type, governance proposal, or keeper function exists to drain the `tieredrewards` module account. Tokens are permanently locked. [4](#0-3) [2](#0-1)

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

**File:** app/app.go (L855-862)
```go
func (app *ChainApp) BlockedAddrs() map[string]bool {
	blockedAddrs := make(map[string]bool)
	for acc := range maccPerms {
		blockedAddrs[authtypes.NewModuleAddress(acc).String()] = !moduleAccsAllowedToReceiveExternalFunds[acc]
	}

	return blockedAddrs
}
```

**File:** x/tieredrewards/types/keys.go (L20-24)
```go
const (
	ModuleName      = "tieredrewards"
	StoreKey        = ModuleName
	RewardsPoolName = "rewards_pool"

```
