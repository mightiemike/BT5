### Title
Permanent NFT Metadata and Burn Lock After Transfer to Non-Creator — (`x/nft/keeper/keeper.go`)

### Summary

`Keeper.EditNFT` and `Keeper.BurnNFT` both require the caller to satisfy **two independent conditions simultaneously**: `IsOwner` (current NFT owner) AND `IsDenomCreator` (original denom creator). Once a denom creator transfers an NFT to any third party, no address can ever satisfy both conditions at once, permanently locking the NFT's metadata and preventing its destruction.

---

### Finding Description

`Keeper.EditNFT` enforces a sequential AND-gate: [1](#0-0) 

`IsOwner` checks that `owner` is the current NFT owner: [2](#0-1) 

`IsDenomCreator` checks that `address` equals the immutable `denom.Creator` field set at issuance time: [3](#0-2) 

`TransferOwner` only requires `IsOwner` — no creator check — so it freely moves ownership away from the creator: [4](#0-3) 

After `A → B` transfer:

| Caller | `IsOwner` | `IsDenomCreator` | `EditNFT` result |
|--------|-----------|------------------|-----------------|
| B (new owner) | ✓ | ✗ | `ErrUnauthorized` |
| A (creator) | ✗ | ✓ | `ErrUnauthorized` |

The same dual-requirement exists in `BurnNFT`, so the NFT also becomes permanently unburnable: [5](#0-4) 

The entry path through `msgServer.EditNFT` passes `msg.Sender` directly as `owner` with no additional authorization bypass: [6](#0-5) 

---

### Impact Explanation

Once a denom creator transfers an NFT to any non-creator address:

- **Metadata is permanently frozen**: no address can call `EditNFT` successfully.
- **NFT is permanently unburnable**: `BurnNFT` has the identical dual-requirement; neither the new owner nor the original creator can burn it.
- The only remaining operation the new owner can perform is another `TransferNFT` (which only requires `IsOwner`), but this does not restore edit or burn capability — it just moves the locked NFT to yet another address.

This is a permanent, irreversible loss of NFT functionality triggered by a normal, expected user action (transferring an NFT).

---

### Likelihood Explanation

The trigger is a completely ordinary sequence of supported transactions: `MsgIssueDenom` → `MsgMintNFT` → `MsgTransferNFT` → `MsgEditNFT`. No special privileges, governance, or key compromise are required. Any user who receives an NFT from its denom creator will discover they cannot edit or burn it.

---

### Recommendation

Remove the `IsDenomCreator` check from `EditNFT` and `BurnNFT`. Ownership (`IsOwner`) is the correct and sufficient authorization for editing or burning an NFT one holds. The creator-only restriction should apply only to minting. Alternatively, if creator-gated editing is intentional, document it clearly and remove `TransferOwner` access for non-creator-held NFTs, or add a separate creator-override edit path that does not require `IsOwner`.

---

### Proof of Concept

```
1. A sends MsgIssueDenom{id: "testdenom", sender: A}
   → denom.Creator = A (immutable)

2. A sends MsgMintNFT{denom_id: "testdenom", id: "nft1", sender: A, recipient: A}
   → NFT owner = A

3. A sends MsgTransferNFT{denom_id: "testdenom", id: "nft1", sender: A, recipient: B}
   → NFT owner = B

4. B sends MsgEditNFT{denom_id: "testdenom", id: "nft1", sender: B, name: "new"}
   → IsOwner(B) = OK, IsDenomCreator(B) = ErrUnauthorized ← BLOCKED

5. A sends MsgEditNFT{denom_id: "testdenom", id: "nft1", sender: A, name: "new"}
   → IsOwner(A) = ErrUnauthorized ← BLOCKED

6. B sends MsgBurnNFT{denom_id: "testdenom", id: "nft1", sender: B}
   → IsOwner(B) = OK, IsDenomCreator(B) = ErrUnauthorized ← BLOCKED

Result: NFT metadata permanently frozen; NFT permanently unburnable.
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

**File:** x/nft/keeper/keeper.go (L128-137)
```go
	nft, err := k.IsOwner(ctx, denomID, tokenID, srcOwner)
	if err != nil {
		return err
	}

	nft.Owner = dstOwner.String()

	k.setNFT(ctx, denomID, nft)
	k.swapOwner(ctx, denomID, tokenID, srcOwner, dstOwner)
	return nil
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

**File:** x/nft/keeper/msg_server.go (L95-108)
```go
func (m msgServer) EditNFT(goCtx context.Context, msg *types.MsgEditNFT) (*types.MsgEditNFTResponse, error) {
	sender, err := sdk.AccAddressFromBech32(msg.Sender)
	if err != nil {
		return nil, err
	}

	ctx := sdk.UnwrapSDKContext(goCtx)
	if err := m.Keeper.EditNFT(ctx, msg.DenomId, msg.Id,
		msg.Name,
		msg.URI,
		msg.Data,
		sender,
	); err != nil {
		return nil, err
```
