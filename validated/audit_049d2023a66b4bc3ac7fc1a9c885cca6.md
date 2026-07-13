### Title
`x/nft-transfer` IBCModule Implements ICS-721 But Its Route Is Never Registered in the IBC Router — (`app/app.go`)

---

### Summary

The `x/nft-transfer` module fully implements `porttypes.IBCModule` for ICS-721 NFT transfers, but its route is never added to the IBC router in `app/app.go`. This is the direct Cosmos SDK analog to the ERC165 `supportsInterface()` omission: the module declares and implements the capability, but the capability-dispatch registry (the IBC router) does not know about it. Any IBC packet targeting the `nft` port cannot be routed, and any NFT escrowed on this chain during a send attempt is permanently locked.

---

### Finding Description

In `app/app.go`, the IBC router is constructed and sealed with exactly three routes:

```go
ibcRouter := porttypes.NewRouter()
ibcRouter.AddRoute(icacontrollertypes.SubModuleName, icaControllerStack)
ibcRouter.AddRoute(icahosttypes.SubModuleName, icaHostStack)
ibcRouter.AddRoute(ibctransfertypes.ModuleName, transferStack)
app.IBCKeeper.SetRouter(ibcRouter)
``` [1](#0-0) 

The `nft-transfer` module (`ModuleName = "nonfungibletokentransfer"`, port `"nft"`) is absent. Yet the module provides a complete `IBCModule` implementation: [2](#0-1) 

Its `InitGenesis` binds the port ID in state: [3](#0-2) 

And its `AppModule` is registered in the module manager alongside the other IBC modules: [4](#0-3) 

The port key and module name are defined as production constants: [5](#0-4) 

The IBC router is the sole dispatch table consulted by IBC core when routing `OnChanOpenInit`, `OnRecvPacket`, `OnAcknowledgementPacket`, and `OnTimeoutPacket`. Because `"nonfungibletokentransfer"` is not in the router, every one of those callbacks will return "route not found" for the `nft` port.

---

### Impact Explanation

**NFT escrow lock-up (asset loss):**

When a user submits `MsgTransfer` via the nft-transfer module, the keeper escrows the NFT on the source chain and commits an IBC packet. The escrow step succeeds because it is handled entirely within the nft-transfer keeper before IBC core is involved. The packet is then stored in IBC state. When the packet times out, a relayer submits `MsgTimeout`; IBC core looks up the `nft` port in the router, finds no route, and returns an error. `OnTimeoutPacket` is never called, so the refund path (`OnTimeoutPacket` → unescrow NFT) is permanently blocked. The NFT is locked in the escrow account with no recovery path.

**ICS-721 channel lifecycle blocked:**

Any attempt to open a channel on the `nft` port (`MsgChannelOpenInit`) will also fail at the router lookup, so no new ICS-721 channels can be established.

The corrupted invariant is: **NFT ownership** — the NFT is removed from the sender's account and placed in the escrow account, but can never be returned because the timeout handler is unreachable.

---

### Likelihood Explanation

Any unprivileged user who calls `MsgTransfer` on the nft-transfer module triggers the escrow. The module's CLI and gRPC endpoints are publicly exposed. The failure is deterministic and 100% reproducible on every send attempt that subsequently times out. No special privileges, leaked keys, or social engineering are required.

---

### Recommendation

Add the nft-transfer IBC route to the router before sealing it in `app/app.go`:

```go
nftTransferIBCModule := nfttransfer.NewIBCModule(app.NftTransferKeeper)
ibcRouter.AddRoute(nfttransfertypes.ModuleName, nftTransferIBCModule)
```

This must be done before `app.IBCKeeper.SetRouter(ibcRouter)` is called, since the router is sealed at that point.

---

### Proof of Concept

1. User calls `MsgTransfer` (nft-transfer) for token `T` on denom `D` with a short timeout.
2. Keeper escrows `T` from the sender's account into the escrow address derived from `portID="nft"` / `channelID`. [6](#0-5) 
3. IBC packet is committed to state.
4. Timeout elapses; relayer submits `MsgTimeout`.
5. IBC core calls `router.Route("nft")` — returns error because the route was never added. [7](#0-6) 
6. `OnTimeoutPacket` is never invoked; the unescrow/refund path in the nft-transfer keeper is never reached.
7. Token `T` is permanently locked in the escrow account. The original owner has lost the NFT with no recourse.

### Citations

**File:** app/app.go (L492-497)
```go
	// Create static IBC router, add transfer route, then set and seal it
	ibcRouter := porttypes.NewRouter()
	ibcRouter.AddRoute(icacontrollertypes.SubModuleName, icaControllerStack)
	ibcRouter.AddRoute(icahosttypes.SubModuleName, icaHostStack)
	ibcRouter.AddRoute(ibctransfertypes.ModuleName, transferStack)
	app.IBCKeeper.SetRouter(ibcRouter)
```

**File:** x/nft-transfer/ibc_module.go (L20-32)
```go
var _ porttypes.IBCModule = IBCModule{}

// IBCModule implements the ICS26 interface for transfer given the transfer keeper.
type IBCModule struct {
	keeper keeper.Keeper
}

// NewIBCModule creates a new IBCModule given the keeper
func NewIBCModule(k keeper.Keeper) IBCModule {
	return IBCModule{
		keeper: k,
	}
}
```

**File:** x/nft-transfer/keeper/genesis.go (L10-16)
```go
func (k Keeper) InitGenesis(ctx sdk.Context, state types.GenesisState) {
	k.SetPort(ctx, state.PortId)

	for _, trace := range state.Traces {
		k.SetClassTrace(ctx, trace)
	}
}
```

**File:** x/nft-transfer/module.go (L26-29)
```go
var (
	_ module.AppModuleBasic = AppModuleBasic{}
	_ porttypes.IBCModule   = IBCModule{}
)
```

**File:** x/nft-transfer/types/keys.go (L10-32)
```go
const (
	// ModuleName Module name defines IBC nft-transfer module name
	ModuleName = "nonfungibletokentransfer"

	// StoreKey is the store key string for IBC nft-transfer
	StoreKey = ModuleName

	// RouterKey is the message route for IBC nft-transfer
	RouterKey = ModuleName

	// QuerierRoute is the querier route for IBC nft-transfer
	QuerierRoute = ModuleName

	// Version defines the current version the IBC nft-transfer
	// module supports
	Version = "ics721-1"

	// PortID is the default port id that nft-transfer module binds to
	PortID = "nft"

	// ClassPrefix is the prefix used for internal SDK NFT representation.
	ClassPrefix = "ibc"
)
```

**File:** x/nft-transfer/keeper/keeper.go (L61-68)
```go
// SetEscrowAddress attempts to save a account to auth module
func (k Keeper) SetEscrowAddress(ctx sdk.Context, portID, channelID string) {
	// create the escrow address for the tokens
	escrowAddress := types.GetEscrowAddress(portID, channelID)
	if !k.authKeeper.HasAccount(ctx, escrowAddress) {
		acc := k.authKeeper.NewAccountWithAddress(ctx, escrowAddress)
		k.authKeeper.SetAccount(ctx, acc)
	}
```
