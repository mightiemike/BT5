### Title
NFT Becomes Permanently Unburnable After Transfer — (`x/nft/keeper/keeper.go`)

### Summary

`Keeper.BurnNFT` requires the caller to simultaneously satisfy both `IsOwner` (current NFT holder) and `IsDenomCreator` (original denom creator). Once a denom creator transfers an NFT to any third party, no address can ever satisfy both conditions at once, making the token permanently unburnable and the denom supply invariant permanently broken.

---

### Finding Description

`Keeper.BurnNFT` enforces two sequential, independent ownership checks: [1](#0-0) 

`IsOwner` verifies `caller == NFT.Owner` (current holder): [2](#0-1) 

`IsDenomCreator` verifies `caller == denom.Creator` (immutable original creator): [3](#0-2) 

`TransferOwner` changes `NFT.Owner` but never changes `denom.Creator`: [4](#0-3) 

After a transfer, the two sets `{current NFT owner}` and `{denom creator}` are disjoint. No single address can satisfy both guards, so `BurnNFT` always returns `ErrUnauthorized` for every possible caller.

The user-facing entry point `msgServer.BurnNFT` calls only `Keeper.BurnNFT` with no bypass: [5](#0-4) 

`BurnNFTUnverified` (which skips `IsDenomCreator`) exists but is only reachable via the IBC transfer path, not via `MsgBurnNFT`: [6](#0-5) 

---

### Impact Explanation

- **Permanent unburnable NFT**: after `A → B` transfer, neither A nor B can call `BurnNFT` successfully.
- **Supply invariant broken**: `decreaseSupply` is never reached, so the on-chain supply counter for that denom never decrements, diverging from the actual number of live tokens.
- **No recovery path**: there is no admin override, governance escape hatch, or alternative `MsgBurnNFT` variant that bypasses `IsDenomCreator`.

---

### Likelihood Explanation

Any denom creator who mints an NFT and transfers it — a completely normal, supported workflow — triggers this condition. The `MsgTransferNFT` path has no restriction preventing this. The scenario is reachable by any user with no special privileges.

---

### Recommendation

Remove the `IsDenomCreator` check from `BurnNFT`. Burning should require only that the caller is the current NFT owner (`IsOwner`). The denom creator check is appropriate for minting and editing, but not for destruction of a token the caller legitimately owns. If the intent is to allow only the denom creator to burn (regardless of current ownership), the check should be on the creator alone, not on the current owner — but that design would also need to be reflected in `TransferOwner` semantics.

---

### Proof of Concept

Keeper-level test (no mocks needed):

```
1. IssueDenom(denomID, creator=A)
2. MintNFT(denomID, tokenID, sender=A, owner=A)
3. TransferOwner(denomID, tokenID, srcOwner=A, dstOwner=B)
4. BurnNFT(denomID, tokenID, owner=B)  → ErrUnauthorized (IsDenomCreator fails)
5. BurnNFT(denomID, tokenID, owner=A)  → ErrUnauthorized (IsOwner fails, A no longer owns it)
6. Assert GetSupply(denomID) == 1      → supply never decremented
```

Both step 4 and step 5 fail with `ErrUnauthorized` via the exact guards at: [1](#0-0) 

The token is permanently locked in state with supply = 1 and no callable burn path.

### Citations

**File:** x/nft/keeper/keeper.go (L121-138)
```go
func (k Keeper) TransferOwner(
	ctx sdk.Context, denomID, tokenID string, srcOwner, dstOwner sdk.AccAddress,
) error {
	if !k.HasDenomID(ctx, denomID) {
		return sdkerrors.Wrapf(types.ErrInvalidDenom, "denom ID %s not exists", denomID)
	}

	nft, err := k.IsOwner(ctx, denomID, tokenID, srcOwner)
	if err != nil {
		return err
	}

	nft.Owner = dstOwner.String()

	k.setNFT(ctx, denomID, nft)
	k.swapOwner(ctx, denomID, tokenID, srcOwner, dstOwner)
	return nil
}
```

**File:** x/nft/keeper/keeper.go (L146-154)
```go
	nft, err := k.IsOwner(ctx, denomID, tokenID, owner)
	if err != nil {
		return err
	}

	_, err = k.IsDenomCreator(ctx, denomID, owner)
	if err != nil {
		return err
	}
```

**File:** x/nft/keeper/keeper.go (L163-179)
```go
// BurnNFTUnverified deletes a specified NFT without verifying if the owner is the creator of denom
// Needed for IBC transfer of NFT
func (k Keeper) BurnNFTUnverified(ctx sdk.Context, denomID, tokenID string, owner sdk.AccAddress) error {
	if !k.HasDenomID(ctx, denomID) {
		return sdkerrors.Wrapf(types.ErrInvalidDenom, "denom ID %s not exists", denomID)
	}

	nft, err := k.IsOwner(ctx, denomID, tokenID, owner)
	if err != nil {
		return err
	}

	k.deleteNFT(ctx, denomID, nft)
	k.deleteOwner(ctx, denomID, tokenID, owner)
	k.decreaseSupply(ctx, denomID)

	return nil
```

**File:** x/nft/keeper/nft.go (L51-53)
```go
	if !owner.Equals(nft.GetOwner()) {
		return types.BaseNFT{}, sdkerrors.Wrapf(types.ErrUnauthorized, "%s is not the owner of %s/%s", owner.String(), denomID, tokenID)
	}
```

**File:** x/nft/keeper/denom.go (L92-94)
```go
	if !creator.Equals(address) {
		return types.Denom{}, sdkerrors.Wrapf(types.ErrUnauthorized, "%s is not the creator of %s", address, denomID)
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
