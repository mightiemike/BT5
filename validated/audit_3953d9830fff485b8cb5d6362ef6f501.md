### Title
Denom Creator Role Permanently Fixed — NFT Owner Has No Edit/Burn Capability After Transfer - (`x/nft/keeper/keeper.go`, `x/nft-transfer/keeper/packet.go`)

### Summary

The `Denom.Creator` field is set once at denom creation and is never updatable. `EditNFT` and `BurnNFT` both require the caller to be simultaneously the NFT owner **and** the denom creator. When an NFT is transferred to a new owner — either via `MsgTransferNFT` or via an IBC-721 receive — the new owner holds the token but cannot edit or burn it. The denom creator role does not follow the NFT. For IBC-received NFTs the denom creator is the escrow module account, which can never sign a user transaction, making `MsgEditNFT` and `MsgBurnNFT` permanently unreachable for every NFT minted on the destination chain.

### Finding Description

**Step 1 — Creator is immutable.**
`SetDenom` writes `Denom.Creator` once and there is no `TransferDenom` / `UpdateDenomCreator` message anywhere in the module. [1](#0-0) 

**Step 2 — EditNFT and BurnNFT enforce a dual check.**
Both operations call `IsOwner` (verifying the sender owns the token) **and** `IsDenomCreator` (verifying the sender is the original denom creator). Passing one check while failing the other causes an `ErrUnauthorized` revert. [2](#0-1) [3](#0-2) 

**Step 3 — TransferOwner does not update the creator.**
`TransferOwner` only updates `nft.Owner` and the owner index; `Denom.Creator` is untouched. [4](#0-3) 

**Step 4 — IBC receive path sets the escrow address as denom creator.**
In `processReceivedPacket`, when a voucher denom does not yet exist on the destination chain, it is created with `escrowAddress` as the creator. NFTs are then minted to the user (`receiver`), not to the escrow address. [5](#0-4) 

The escrow address is a deterministic module account (`types.GetEscrowAddress(destPort, destChannel)`). It holds no private key and can never sign a `MsgEditNFT` or `MsgBurnNFT` transaction. Every NFT minted on the destination chain therefore has an owner who can transfer the token but can never edit or burn it.

**Step 5 — The IBC outgoing path uses `BurnNFTUnverified`, bypassing the creator check.**
The module itself can burn tokens during a return transfer because it calls `BurnNFTUnverified`, which skips the `IsDenomCreator` guard. This path is not available to end users. [6](#0-5) 

### Impact Explanation

Any user who receives an NFT via IBC-721 (`MsgTransfer` → `OnRecvPacket`) owns a token whose denom creator is the escrow module account. That user:

- **Cannot call `MsgEditNFT`** — the `IsDenomCreator` check at `keeper.go:98` will always fail.
- **Cannot call `MsgBurnNFT`** — the `IsDenomCreator` check at `keeper.go:151` will always fail.

The same applies on the native chain whenever the original denom creator transfers their NFTs to a third party: the new owner holds the token but is permanently locked out of edit and burn operations. The denom creator role is irrecoverable once the creator key is lost or the NFT is transferred away.

### Likelihood Explanation

IBC NFT transfer is a core, documented, production feature of the chain. Every single NFT received via `OnRecvPacket` on the destination chain is affected — this is not an edge case. Any user who receives an IBC NFT and later tries to edit or burn it via the standard CLI or transaction path will be silently blocked.

### Recommendation

1. **Add a `MsgTransferDenom` message** that allows the current `Denom.Creator` to transfer the creator role to a new address, analogous to `MsgTransferNFT`.
2. **For IBC-received denoms**, set the `receiver` (or a governance-controlled address) as the denom creator instead of the escrow address, or relax the `IsDenomCreator` check in `EditNFT`/`BurnNFT` so that the NFT owner alone is sufficient.
3. Alternatively, separate the "denom administrator" role from the per-token owner role so that edit/burn permissions follow token ownership rather than denom creation.

### Proof of Concept

1. On chain A, user Alice issues denom `myclass` and mints token `tok1` to herself.
2. Alice sends `tok1` to Bob on chain B via `MsgTransfer` (IBC-721).
3. On chain B, `processReceivedPacket` creates denom `ibc/<hash>` with `escrowAddress` as creator and mints `tok1` to Bob.
4. Bob submits `MsgEditNFT{DenomId: "ibc/<hash>", Id: "tok1", Sender: bob}`.
5. `EditNFT` calls `IsDenomCreator(ctx, "ibc/<hash>", bob)` → `bob != escrowAddress` → `ErrUnauthorized`.
6. Bob submits `MsgBurnNFT{DenomId: "ibc/<hash>", Id: "tok1", Sender: bob}`.
7. `BurnNFT` calls `IsDenomCreator(ctx, "ibc/<hash>", bob)` → same failure.

Bob owns the NFT but has no capability to edit or burn it. The escrow address that holds the creator role can never sign a transaction. [7](#0-6) [8](#0-7)

### Citations

**File:** x/nft/keeper/denom.go (L26-39)
```go
func (k Keeper) SetDenom(ctx sdk.Context, denom types.Denom) error {
	if k.HasDenomID(ctx, denom.Id) {
		return sdkerrors.Wrapf(types.ErrInvalidDenom, "denomID %s has already exists", denom.Id)
	}

	if k.HasDenomNm(ctx, denom.Name) {
		return sdkerrors.Wrapf(types.ErrInvalidDenom, "denomName %s has already exists", denom.Name)
	}

	store := ctx.KVStore(k.storeKey)
	bz := k.cdc.MustMarshal(&denom)
	store.Set(types.KeyDenomID(denom.Id), bz)
	store.Set(types.KeyDenomName(denom.Name), []byte(denom.Id))
	return nil
```

**File:** x/nft/keeper/denom.go (L79-97)
```go
// IsDenomCreator checks if address is the creator of Denom
// Return the Denom if true, an error otherwise
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
}
```

**File:** x/nft/keeper/keeper.go (L84-118)
```go
// EditNFT updates an already existing NFT
func (k Keeper) EditNFT(
	ctx sdk.Context, denomID, tokenID, tokenNm,
	tokenURI, tokenData string, owner sdk.AccAddress,
) error {
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

	if types.Modified(tokenNm) {
		nft.Name = tokenNm
	}

	if types.Modified(tokenURI) {
		nft.URI = tokenURI
	}

	if types.Modified(tokenData) {
		nft.Data = tokenData
	}

	k.setNFT(ctx, denomID, nft)

	return nil
}
```

**File:** x/nft/keeper/keeper.go (L120-138)
```go
// TransferOwner transfers the ownership of the given NFT to the new owner
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

**File:** x/nft/keeper/keeper.go (L140-161)
```go
// BurnNFT deletes a specified NFT
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

**File:** x/nft-transfer/keeper/packet.go (L112-117)
```go
		} else {
			// we are sink chain, burn the voucher
			if err := k.nftKeeper.BurnNFTUnverified(ctx, classID, tokenID, sender); err != nil {
				return channeltypes.Packet{}, err
			}
		}
```

**File:** x/nft-transfer/keeper/packet.go (L150-185)
```go
	// create the escrow address for creating denom and minting nft
	escrowAddress := types.GetEscrowAddress(packet.GetDestPort(), packet.GetDestChannel())

	if isAwayFromOrigin {
		// since SendPacket did not prefix the classID, we must prefix classID here
		classPrefix := types.GetClassPrefix(packet.GetDestPort(), packet.GetDestChannel())
		// NOTE: sourcePrefix contains the trailing "/"
		prefixedClassID := classPrefix + data.ClassId

		// construct the class trace from the full raw classID
		classTrace := types.ParseClassTrace(prefixedClassID)
		if !k.HasClassTrace(ctx, classTrace.Hash()) {
			k.SetClassTrace(ctx, classTrace)
		}

		voucherClassID := classTrace.IBCClassID()

		if !k.nftKeeper.HasDenomID(ctx, voucherClassID) {
			if err := k.nftKeeper.IssueDenom(ctx, voucherClassID, voucherClassID, "", data.ClassUri, escrowAddress); err != nil {
				return err
			}
		}
		sdkCtx := sdk.UnwrapSDKContext(ctx)
		sdkCtx.EventManager().EmitEvent(
			sdk.NewEvent(
				types.EventTypeClassTrace,
				sdk.NewAttribute(types.AttributeKeyTraceHash, classTrace.Hash().String()),
				sdk.NewAttribute(types.AttributeKeyClassID, voucherClassID),
			),
		)

		for i, tokenID := range data.TokenIds {
			if err := k.nftKeeper.MintNFT(ctx, voucherClassID, tokenID, "", data.TokenUris[i], "", escrowAddress, receiver); err != nil {
				return err
			}
		}
```
