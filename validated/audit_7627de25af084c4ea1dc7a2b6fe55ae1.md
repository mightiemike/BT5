The `BurnNFT` keeper function at lines 141–161 of `x/nft/keeper/keeper.go` imposes **two simultaneous, mutually exclusive guards** when an NFT has been minted to a non-creator address: [1](#0-0) 

```
BurnNFT(denomID, tokenID, caller):
  1. IsOwner(caller)       → caller must be current token owner
  2. IsDenomCreator(caller) → caller must be denom creator
```

After `addrA` (denom creator) mints `tokenID` to `addrB`:

| Caller | `IsOwner` result | `IsDenomCreator` result | Net |
|--------|-----------------|------------------------|-----|
| `addrA` | **ErrUnauthorized** (addrB owns it) | OK | Fails at step 1 |
| `addrB` | OK | **ErrUnauthorized** (addrA is creator) | Fails at step 2 |

Neither party can ever burn the token. The NFT is permanently locked in supply.

---

### Title
Denom creator cannot burn NFTs minted to non-creator addresses — (`x/nft/keeper/keeper.go`)

### Summary
`BurnNFT` requires the caller to simultaneously satisfy `IsOwner` **and** `IsDenomCreator`. Once an NFT is minted to any address other than the denom creator, no address can satisfy both conditions, making the token permanently unburnable.

### Finding Description
`keeper.BurnNFT` calls `IsOwner` first, then `IsDenomCreator`, both on the same `owner` argument: [2](#0-1) 

`IsOwner` checks `nft.GetOwner() == owner`: [3](#0-2) 

`IsDenomCreator` checks `denom.Creator == address`: [4](#0-3) 

After a transfer or a mint-to-recipient, the NFT owner and the denom creator are different addresses. No single address can satisfy both guards simultaneously, so `BurnNFT` always returns `ErrUnauthorized` for every possible caller.

Note that `MintNFT` only requires `IsDenomCreator` (not ownership), so minting to a third party is a fully supported, normal operation: [5](#0-4) 

The `MsgServer.BurnNFT` passes the tx signer directly to `keeper.BurnNFT` with no alternative path: [6](#0-5) 

### Impact Explanation
- **NFT supply permanently inflated**: `decreaseSupply` is never reached for any NFT whose owner ≠ denom creator. The on-chain supply counter diverges from reality.
- **Denom creator loses collection control**: The creator cannot retire, recall, or destroy tokens they issued to recipients, breaking the intended administrative model.
- **Recipient also locked out**: The token holder cannot burn either, so the token is permanently stuck.

### Likelihood Explanation
The normal, documented use-case — minting an NFT to a recipient (`msg.Recipient != msg.Sender`) via `MsgMintNFT` — immediately triggers this condition. Any denom creator who mints to a third party (the primary purpose of the recipient field) is affected on the very next `MsgBurnNFT` attempt.

### Recommendation
Separate the authorization logic: allow burning if the caller is **either** the current owner **or** the denom creator (not both simultaneously). For example:

```go
func (k Keeper) BurnNFT(ctx sdk.Context, denomID, tokenID string, sender sdk.AccAddress) error {
    nft, err := k.GetNFT(ctx, denomID, tokenID)
    if err != nil { return err }

    isOwner := sender.Equals(nft.GetOwner())
    _, creatorErr := k.IsDenomCreator(ctx, denomID, sender)
    isDenomCreator := creatorErr == nil

    if !isOwner && !isDenomCreator {
        return sdkerrors.Wrapf(types.ErrUnauthorized, ...)
    }
    // proceed with deletion
}
```

### Proof of Concept
```
1. addrA issues denomID  → addrA is denom creator
2. addrA sends MsgMintNFT{DenomId: denomID, Id: "t1", Recipient: addrB}
   → NFT t1 owner = addrB
3. addrA sends MsgBurnNFT{DenomId: denomID, Id: "t1", Sender: addrA}
   → keeper.IsOwner(addrA) → ErrUnauthorized  ✗
4. addrB sends MsgBurnNFT{DenomId: denomID, Id: "t1", Sender: addrB}
   → keeper.IsDenomCreator(addrB) → ErrUnauthorized  ✗
5. NFT t1 is permanently unburnable; supply counter never decremented.
```

### Citations

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
