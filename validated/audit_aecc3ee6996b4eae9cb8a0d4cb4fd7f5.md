### Title
NFT Denom Creator Retains Permanent Minting Privilege After Token Transfer, Enabling Unbounded Collection Supply Inflation — (File: `x/nft/keeper/keeper.go`)

### Summary
The `x/nft` module permanently binds the minting, editing, and burning privileges of a denom to the original `Denom.Creator` address. When a creator sells NFT tokens to buyers via `MsgTransferNFT`, the creator retains the ability to mint unlimited additional tokens into the same denom at any time. There is no mechanism to transfer or relinquish the creator role. This is a direct structural analog to the reported race condition: the original privileged party retains the ability to take actions that harm the new token holder's position after the transfer.

### Finding Description
`IssueDenom` stores the sender as `Denom.Creator` in the `Denom` struct. This field is written once and never updated. `MintNFT` gates minting exclusively on `IsDenomCreator`, which compares the sender against `Denom.Creator`. `TransferNFT` calls `TransferOwner`, which only swaps the per-token `nft.Owner` field and the owner index — it does not touch `Denom.Creator`. There is no `TransferDenomCreator` message or any other mechanism to move the creator role.

Consequently, after a creator sells NFT tokens to buyers, the creator retains the ability to:
1. Mint unlimited additional tokens into the same denom (`MsgMintNFT`)
2. Edit any token they still own in that denom (`MsgEditNFT`)
3. Burn any token they still own in that denom (`MsgBurnNFT`)

The creator can exercise these privileges in the same block as the transfer (ordering their `MsgMintNFT` before the `MsgTransferNFT` in the block), or at any point afterward.

### Impact Explanation
A buyer who purchases an NFT token from a denom receives a token whose collection supply can be inflated to any size by the original creator at any time. The scarcity property — the core value proposition of the purchased NFT — is permanently under the unilateral control of the creator. The buyer has no recourse and no on-chain visibility into whether the creator will mint more tokens. The corrupted on-chain value is the denom's `supply` counter and the set of tokens in the collection, both of which the creator can expand without limit after the sale.

### Likelihood Explanation
High. Any denom creator who sells NFT tokens to third parties retains this capability by default. No special conditions, leaked keys, or privileged roles beyond the original `IssueDenom` sender are required. The entry path is the standard `MsgMintNFT` transaction, callable by any account that is the `Denom.Creator`.

### Recommendation
Introduce a `MsgTransferDenomCreator` message that atomically updates `Denom.Creator` to a new address, allowing a creator to irrevocably relinquish minting control. Alternatively, add a supply-cap field to `Denom` that the creator can set and lock, preventing further minting once the cap is reached. At minimum, document clearly that the creator retains permanent minting rights over all tokens in the denom regardless of who holds them.

### Proof of Concept
1. Alice calls `MsgIssueDenom{Id: "art", Sender: alice}` → `Denom.Creator = alice`.
2. Alice calls `MsgMintNFT{DenomId: "art", Id: "token1", Sender: alice, Recipient: alice}` — 1 token exists.
3. Bob pays Alice off-chain; Alice calls `MsgTransferNFT{DenomId: "art", Id: "token1", Sender: alice, Recipient: bob}`.
4. `TransferOwner` sets `nft.Owner = bob` and updates the owner index. `Denom.Creator` remains `alice`.
5. Alice calls `MsgMintNFT{DenomId: "art", Id: "token2", Sender: alice, Recipient: alice}` — `IsDenomCreator` passes because `Denom.Creator == alice`. A second token now exists in the same denom.
6. Alice repeats step 5 indefinitely. Bob's "token1" is now 1 of N, not 1 of 1.

**Root cause lines:**

`Denom.Creator` is immutable after `SetDenom`: [1](#0-0) 

`MintNFT` gates only on `IsDenomCreator`, with no check on whether the creator has sold tokens: [2](#0-1) 

`TransferOwner` only updates the per-token owner, leaving `Denom.Creator` untouched: [3](#0-2) 

`IsDenomCreator` compares against the immutable `Denom.Creator` field: [4](#0-3)

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

**File:** x/nft/keeper/keeper.go (L72-82)
```go
func (k Keeper) MintNFT(
	ctx sdk.Context, denomID, tokenID, tokenNm,
	tokenURI, tokenData string, sender, owner sdk.AccAddress,
) error {
	_, err := k.IsDenomCreator(ctx, denomID, sender)
	if err != nil {
		return err
	}

	return k.MintNFTUnverified(ctx, denomID, tokenID, tokenNm, tokenURI, tokenData, owner)
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
