### Title
NFT-Transfer Escrow Addresses Are Unblocked BaseAccounts, Allowing Permanent Fungible Token Loss — (`app/app.go`, `x/nft-transfer/keeper/keeper.go`, `x/nft-transfer/types/keys.go`)

---

### Summary

The `nonfungibletokentransfer` module name is absent from `maccPerms` and therefore absent from the `BlockedAddrs` map passed to `bankkeeper.NewBaseKeeper`. NFT escrow addresses are created as plain `BaseAccount`s (not module accounts) with no private key. Because they are not blocked, any user can `MsgSend` fungible tokens to them. Those tokens are permanently inaccessible — no private key exists for the address and no module has authority to recover them — constituting direct, irreversible fund loss.

---

### Finding Description

**Root cause 1 — `maccPerms` omits `nonfungibletokentransfer`** [1](#0-0) 

The `maccPerms` map registers every module that should be treated as a module account. `nonfungibletokentransfer` (`types.ModuleName`) is not present.

**Root cause 2 — `BlockedAddrs()` is derived exclusively from `maccPerms`** [2](#0-1) 

`BlockedAddrs()` iterates only `maccPerms`. Because `nonfungibletokentransfer` is absent, no NFT escrow address ever appears in the blocked set passed to `bankkeeper.NewBaseKeeper`. [3](#0-2) 

**Root cause 3 — Escrow addresses are created as `BaseAccount`s**

`SetEscrowAddress` calls `authKeeper.NewAccountWithAddress`, which returns a `BaseAccount`, not a `ModuleAccount`: [4](#0-3) 

**Root cause 4 — Escrow address has no private key**

`GetEscrowAddress` derives the address from a SHA-256 hash of the port/channel identifiers (ADR-028). No private key corresponds to this address: [5](#0-4) 

**Combined effect:** The escrow address is a `BaseAccount`, not in `BlockedAddrs`, and has no private key. The bank module's `MsgSend` handler checks `BlockedAddrs` before rejecting a send; since the escrow address is absent, the send succeeds. The deposited fungible tokens are permanently locked — no module controls the address and no key can sign a recovery transaction.

---

### Impact Explanation

Any user (or attacker tricking a user) who sends fungible tokens to a known NFT escrow address suffers **irreversible fund loss**. The tokens are credited to the `BaseAccount` at the escrow address, but:

- No module tracks or can sweep them.
- No private key exists to sign a recovery transaction.
- The bank module does not block the send.

This is a direct, concrete balance delta: the sender's balance decreases and the tokens are permanently unrecoverable. The accounting invariant — that module-controlled addresses must be blocked from receiving external fungible token transfers — is violated.

---

### Likelihood Explanation

- The escrow address for any active NFT-transfer channel is **publicly computable** from `GetEscrowAddress(portID, channelID)` and is even exposed via the CLI query `nft-transfer escrow-address`.
- A `MsgSend` to that address requires only a standard signed transaction — no special privilege.
- Accidental sends (e.g., copy-paste of an escrow address) are a realistic user-error scenario on a live chain.

---

### Recommendation

Add `nonfungibletokentransfer` to `maccPerms` in `app/app.go`:

```go
nfttransfertypes.ModuleName: nil,
```

This causes `BlockedAddrs()` to include the module's derived address. Additionally, `SetEscrowAddress` should create a `ModuleAccount` (or the escrow address should be explicitly added to the blocked set) so the bank module rejects external fungible token sends to it, consistent with how `ibctransfertypes.ModuleName` is handled. [1](#0-0) 

---

### Proof of Concept

```go
func TestNFTEscrowAddressNotBlocked(t *testing.T) {
    app := setupApp(t)
    ctx := app.NewContext(false)

    // Compute the escrow address for a known channel
    escrow := nfttransfertypes.GetEscrowAddress("nft", "channel-0")

    // Assert it is NOT in BlockedAddrs (demonstrates the bug)
    blocked := app.BlockedAddrs()
    require.False(t, blocked[escrow.String()],
        "escrow address must be blocked but is not")

    // Demonstrate MsgSend succeeds to the escrow address
    sender := createFundedAccount(t, ctx, app, sdk.NewCoins(sdk.NewInt64Coin("basecro", 1000)))
    msg := banktypes.NewMsgSend(sender, escrow, sdk.NewCoins(sdk.NewInt64Coin("basecro", 100)))
    _, err := app.BankKeeper.Send(ctx, msg)
    require.NoError(t, err, "send to unblocked escrow address must succeed — tokens are now permanently lost")
}
```

### Citations

**File:** app/app.go (L153-165)
```go
	maccPerms = map[string][]string{
		authtypes.FeeCollectorName:         nil,
		distrtypes.ModuleName:              nil,
		minttypes.ModuleName:               {authtypes.Minter},
		stakingtypes.BondedPoolName:        {authtypes.Burner, authtypes.Staking},
		stakingtypes.NotBondedPoolName:     {authtypes.Burner, authtypes.Staking},
		govtypes.ModuleName:                {authtypes.Burner},
		ibctransfertypes.ModuleName:        {authtypes.Minter, authtypes.Burner},
		icatypes.ModuleName:                nil,
		tieredrewardstypes.RewardsPoolName: nil,
		// Legacy, not used anymore. Created by the previous implementation which uses this module to staking on behalf of users.
		tieredrewardstypes.ModuleName: {authtypes.Staking},
	}
```

**File:** app/app.go (L325-332)
```go
	app.BankKeeper = bankkeeper.NewBaseKeeper(
		appCodec,
		runtime.NewKVStoreService(keys[banktypes.StoreKey]),
		app.AccountKeeper,
		app.BlockedAddrs(),
		authAddr,
		logger,
	)
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

**File:** x/nft-transfer/keeper/keeper.go (L62-68)
```go
func (k Keeper) SetEscrowAddress(ctx sdk.Context, portID, channelID string) {
	// create the escrow address for the tokens
	escrowAddress := types.GetEscrowAddress(portID, channelID)
	if !k.authKeeper.HasAccount(ctx, escrowAddress) {
		acc := k.authKeeper.NewAccountWithAddress(ctx, escrowAddress)
		k.authKeeper.SetAccount(ctx, acc)
	}
```

**File:** x/nft-transfer/types/keys.go (L45-56)
```go
func GetEscrowAddress(portID, channelID string) sdk.AccAddress {
	// a slash is used to create domain separation between port and channel identifiers to
	// prevent address collisions between escrow addresses created for different channels
	contents := fmt.Sprintf("%s/%s", portID, channelID)

	// ADR 028 AddressHash construction
	preImage := []byte(Version)
	preImage = append(preImage, 0)
	preImage = append(preImage, contents...)
	hash := sha256.Sum256(preImage)
	return hash[:20]
}
```
