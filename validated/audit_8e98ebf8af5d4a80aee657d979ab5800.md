### Title
Permanent Loss of NFT Burn/Edit Control After Transfer Away From Denom Creator — (File: x/nft/keeper/keeper.go)

### Summary
`BurnNFT` and `EditNFT` require the caller to satisfy **both** `IsOwner` and `IsDenomCreator` simultaneously. `TransferNFT` only moves NFT ownership; it never updates the denom's `Creator` field. Once the denom creator transfers any NFT to another address, that NFT becomes permanently unburnable and uneditable by anyone — the new owner is not the creator, and the creator is no longer the owner. There is no `TransferDenomCreator` message and no governance/admin override path.

### Finding Description
`BurnNFT` enforces two independent checks in sequence:

```go
nft, err := k.IsOwner(ctx, denomID, tokenID, owner)   // must be NFT owner
...
_, err = k.IsDenomCreator(ctx, denomID, owner)         // must also be denom creator
``` [1](#0-0) 

`EditNFT` applies the identical dual-check: [2](#0-1) 

`TransferOwner` (called by `MsgTransferNFT`) updates only `nft.Owner` and the owner-index; it never touches `denom.Creator`: [3](#0-2) 

The `Denom` struct stores a single immutable `creator` string: [4](#0-3) 

`IsDenomCreator` compares the caller address against this fixed field and returns an error if they differ: [5](#0-4) 

There is no `MsgTransferDenom`, no governance-level burn override, and no admin escape hatch. `BurnNFTUnverified` exists but is only called internally for IBC packet processing — it is not reachable via any user-facing message: [6](#0-5) 

The existing test suite explicitly documents the broken state — it transfers the NFT back to the creator as the only workaround — but provides no protocol-level remedy when the new owner refuses or is unavailable: [7](#0-6) 

### Impact Explanation
Once an NFT is transferred away from the denom creator, it is permanently unburnable and uneditable by **anyone**:
- The new owner cannot burn or edit it (not the creator).
- The creator cannot burn or edit it (not the owner).
- No governance proposal, no admin message, and no IBC path can recover this state for a normal on-chain NFT.

The denom creator permanently loses administrative control over every NFT in their collection that has ever been transferred. NFTs that should be burnable remain in circulation forever, and their metadata is frozen.

### Likelihood Explanation
Transferring minted NFTs to other addresses is the primary purpose of `MsgTransferNFT` and is a routine, expected operation for any NFT collection. Any denom creator who mints NFTs to themselves and then transfers them — or mints directly to recipients and later needs to burn/edit — will trigger this state. No special attacker knowledge or privileged access is required; a single standard `MsgTransferNFT` transaction is sufficient.

### Recommendation
One of the following mitigations should be applied:

1. **Allow the denom creator to burn/edit any NFT in their denom regardless of current ownership** — remove the `IsOwner` check from `BurnNFT`/`EditNFT` when the caller is the denom creator.
2. **Add a `MsgTransferDenomCreator` message** so the creator role can be explicitly transferred, keeping the dual-check but making the invariant maintainable.
3. **Separate burn authority from edit authority** — allow the current NFT owner to burn without requiring creator status, and restrict editing to the creator only.

### Proof of Concept
```
Step 0:
  CREATOR issues denom "myDenom" via MsgIssueDenom.
  denom.Creator = CREATOR (fixed forever).

Step 1:
  CREATOR mints tokenID "nft1" to themselves via MsgMintNFT
  (sender=CREATOR, recipient=CREATOR).
  nft1.Owner = CREATOR.

Step 2:
  CREATOR transfers "nft1" to ALICE via MsgTransferNFT.
  nft1.Owner = ALICE.
  denom.Creator = CREATOR (unchanged).

Step 3:
  ALICE submits MsgBurnNFT(sender=ALICE, denom_id="myDenom", id="nft1").
  → BurnNFT: IsOwner(ALICE) ✓, IsDenomCreator(ALICE) ✗
  → Error: "ALICE is not the creator of myDenom"

Step 4:
  CREATOR submits MsgBurnNFT(sender=CREATOR, denom_id="myDenom", id="nft1").
  → BurnNFT: IsOwner(CREATOR) ✗
  → Error: "CREATOR is not the owner of myDenom/nft1"

Result:
  "nft1" is permanently unburnable and uneditable by anyone.
  The denom creator has permanently lost administrative control
  over this NFT with no on-chain recovery path.
```

### Citations

**File:** x/nft/keeper/keeper.go (L93-101)
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

**File:** x/nft/keeper/keeper.go (L163-180)
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
}
```

**File:** proto/chainmain/nft/v1/nft.proto (L23-32)
```text
message Denom {
  option (gogoproto.equal) = true;

  string id      = 1;
  string name    = 2;
  string schema  = 3;
  string creator = 4;
  string uri     = 5; // This was added because Cosmos SDK's native NFT module has uri as a parameter for class which is
                      // needed for nft transfers
}
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

**File:** x/nft/keeper/keeper_test.go (L172-188)
```go
	// Transfer nft to an address which is not the creator of denom
	err = suite.keeper.TransferOwner(suite.ctx, denomID, tokenID, address, address2)
	suite.NoError(err)

	// BurnNFT should fail when address is not the creator of denom
	err = suite.keeper.BurnNFT(suite.ctx, denomID, tokenID, address2)
	if suite.Error(err) {
		suite.EqualError(err, fmt.Sprintf("%s is not the creator of %s: %s", address2, denomID, types.ErrUnauthorized))
	}

	// Transfer nft back to the creator of denom
	err = suite.keeper.TransferOwner(suite.ctx, denomID, tokenID, address2, address)
	suite.NoError(err)

	// BurnNFT shouldn't fail when NFT exists and address is owner of nft and creator of denom
	err = suite.keeper.BurnNFT(suite.ctx, denomID, tokenID, address)
	suite.NoError(err)
```
