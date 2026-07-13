The code confirms this is a real vulnerability. Let me trace through all the relevant functions precisely.

**`BurnNFT` requires BOTH checks simultaneously:** [1](#0-0) 

Line 146 calls `IsOwner` (caller must be current owner), and line 151 calls `IsDenomCreator` (caller must be the denom creator). Both must pass for the same `owner` address.

**`IsOwner` checks current NFT ownership:** [2](#0-1) 

**`IsDenomCreator` checks the denom's stored creator field:** [3](#0-2) 

**`BurnNFTUnverified` skips `IsDenomCreator` but is only called from the IBC sink-chain path:** [4](#0-3) 

**`MsgServer.BurnNFT` only routes to the verified version:** [5](#0-4) 

There is no `MsgServer` method or any other externally reachable transaction path that calls `BurnNFTUnverified`.

---

### Title
Logical deadlock in `BurnNFT` makes any NFT permanently unburnable after transfer from creator — (`x/nft/keeper/keeper.go`)

### Summary
`BurnNFT` requires the caller to simultaneously satisfy `IsOwner` (current NFT owner) and `IsDenomCreator` (original denom creator). Once the creator transfers an NFT to any other address, no party can ever satisfy both conditions at once, making the NFT permanently unburnable via any standard transaction path. The supply counter is never decremented.

### Finding Description
`BurnNFT` at lines 146 and 151 of `x/nft/keeper/keeper.go` applies two sequential guards to the same `owner` address:

1. `IsOwner(ctx, denomID, tokenID, owner)` — passes only if `owner` is the current NFT holder.
2. `IsDenomCreator(ctx, denomID, owner)` — passes only if `owner` is the address stored as `denom.Creator`.

After `TransferOwner` (via `MsgTransferNFT`) moves the NFT from creator to any other address:
- The **new owner** fails `IsDenomCreator` (they are not the creator).
- The **creator** fails `IsOwner` (they no longer hold the NFT).

`BurnNFTUnverified` (lines 165–179) omits the `IsDenomCreator` check and would allow the current owner to burn, but it is only invoked from `createOutgoingPacket` in `x/nft-transfer/keeper/packet.go` (line 114), exclusively on the IBC sink-chain path. It is not exposed through any `MsgServer` handler or any other externally reachable transaction type.

The same deadlock applies when the creator mints directly to a recipient (`MintNFT` accepts a separate `owner` parameter distinct from `sender`): the NFT is born already unburnable.

### Impact Explanation
- `decreaseSupply` is never called for the affected token; `GetTotalSupply` for the denom is permanently inflated by 1 per such NFT.
- The NFT record persists in the KV store indefinitely with no removal path available to any on-chain actor via standard Cosmos SDK transactions.
- Any application or protocol layer relying on `GetTotalSupply` for accounting (e.g., IBC class-trace supply invariants, marketplace integrations) will observe a permanently incorrect value.

### Likelihood Explanation
The trigger is the ordinary, documented workflow: a creator mints an NFT to a user (or mints to themselves and then transfers). This is the primary intended use of the module. Every such NFT is affected. No special privileges, governance, or key compromise are required.

### Recommendation
Remove the `IsDenomCreator` check from `BurnNFT`. The correct invariant is: **the current owner of an NFT may burn it**. If the protocol requires creator-only burn semantics, the creator must reclaim ownership first (via `TransferNFT`) before burning — but this should be documented, not silently enforced by a deadlock. Alternatively, expose `BurnNFTUnverified` (renamed appropriately) as a `MsgServer` handler gated solely on `IsOwner`.

### Proof of Concept
```go
// keeper_test.go style
k.IssueDenom(ctx, "testdenom", "Test", "", "", creator)
k.MintNFT(ctx, "testdenom", "token1", "", "", "", creator, user) // mint directly to user

// user attempts burn — IsOwner passes, IsDenomCreator fails
err := k.BurnNFT(ctx, "testdenom", "token1", user)
require.Error(t, err) // ErrUnauthorized: user is not the creator

// creator attempts burn — IsOwner fails (creator doesn't own it)
err = k.BurnNFT(ctx, "testdenom", "token1", creator)
require.Error(t, err) // ErrUnauthorized: creator is not the owner

// supply is still 1, NFT is permanently stuck
supply := k.GetTotalSupply(ctx, "testdenom")
require.Equal(t, uint64(1), supply)
```

### Citations

**File:** x/nft/keeper/keeper.go (L141-161)
```go
func (k Keeper) BurnNFT(ctx sdk.Context, denomID, tokenID string, owner sdk.AccAddress) error {
	if !k.HasDenomID(ctx, denomID) {
		return sdkerrors.Wrapf(types.ErrInvalidDenom, "denom ID %s not exists", denomID)
	}

	nft, err := k.IsOwner(ctx, denomID, tokenID, owner)
	if err != nil {
		return err
	}

	_, err = k.IsDenomCreator(ctx, denomID, owner)
	if err != nil {
		return err
	}

	k.deleteNFT(ctx, denomID, nft)
	k.deleteOwner(ctx, denomID, tokenID, owner)
	k.decreaseSupply(ctx, denomID)

	return nil
}
```

**File:** x/nft/keeper/nft.go (L45-56)
```go
func (k Keeper) IsOwner(ctx sdk.Context, denomID, tokenID string, owner sdk.AccAddress) (types.BaseNFT, error) {
	nft, err := k.GetNFT(ctx, denomID, tokenID)
	if err != nil {
		return types.BaseNFT{}, err
	}

	if !owner.Equals(nft.GetOwner()) {
		return types.BaseNFT{}, sdkerrors.Wrapf(types.ErrUnauthorized, "%s is not the owner of %s/%s", owner.String(), denomID, tokenID)
	}

	return nft.(types.BaseNFT), nil
}
```

**File:** x/nft/keeper/denom.go (L81-96)
```go
func (k Keeper) IsDenomCreator(ctx sdk.Context, denomID string, address sdk.AccAddress) (types.Denom, error) {
	denom, err := k.GetDenom(ctx, denomID)
	if err != nil {
		return types.Denom{}, err
	}

	creator, err := sdk.AccAddressFromBech32(denom.Creator)
	if err != nil {
		panic(err)
	}

	if !creator.Equals(address) {
		return types.Denom{}, sdkerrors.Wrapf(types.ErrUnauthorized, "%s is not the creator of %s", address, denomID)
	}

	return denom, nil
```

**File:** x/nft-transfer/keeper/packet.go (L112-116)
```go
		} else {
			// we are sink chain, burn the voucher
			if err := k.nftKeeper.BurnNFTUnverified(ctx, classID, tokenID, sender); err != nil {
				return channeltypes.Packet{}, err
			}
```

**File:** x/nft/keeper/msg_server.go (L166-174)
```go
func (m msgServer) BurnNFT(goCtx context.Context, msg *types.MsgBurnNFT) (*types.MsgBurnNFTResponse, error) {
	sender, err := sdk.AccAddressFromBech32(msg.Sender)
	if err != nil {
		return nil, err
	}

	ctx := sdk.UnwrapSDKContext(goCtx)
	if err := m.Keeper.BurnNFT(ctx, msg.DenomId, msg.Id, sender); err != nil {
		return nil, err
```
