### Title
NFT Permanently Locked After Transfer — `EditNFT`/`BurnNFT` Require Simultaneous `IsOwner` AND `IsDenomCreator`, Impossible After Transfer - ([File: x/nft/keeper/keeper.go])

---

### Summary

`EditNFT` and `BurnNFT` both require the caller to simultaneously satisfy `IsOwner` **and** `IsDenomCreator`. Once the denom creator transfers an NFT to any third party, no address can ever satisfy both conditions at once, permanently locking the NFT's metadata and making it indestructible.

---

### Finding Description

`EditNFT` enforces two sequential guards: [1](#0-0) 

`BurnNFT` enforces the same two sequential guards: [2](#0-1) 

`TransferOwner`, however, only checks `IsOwner` — no creator check: [3](#0-2) 

`IsDenomCreator` compares the caller against the immutable `denom.Creator` field set at `IssueDenom` time and never updated: [4](#0-3) 

`IsOwner` compares the caller against the current `nft.Owner` field, which changes on every transfer: [5](#0-4) 

After `TransferOwner(A → B)`:
- `A` fails `IsOwner` (A is no longer owner) → `EditNFT`/`BurnNFT` revert at line 93/146
- `B` fails `IsDenomCreator` (B is not the creator) → `EditNFT`/`BurnNFT` revert at line 98/151
- No other address can satisfy both simultaneously
- There is no admin override, governance escape hatch, or `BurnNFTUnverified` path accessible via a user-facing message

Note: `BurnNFTUnverified` exists but is only called internally for IBC transfers, not exposed as a `MsgServer` handler: [6](#0-5) 

---

### Impact Explanation

Any NFT minted by the denom creator and subsequently transferred to a non-creator address becomes permanently:
1. **Uneditable** — metadata (name, URI, data) is frozen forever
2. **Indestructible** — `BurnNFT` is permanently blocked; the token cannot be removed from state

This is a permanent, irrecoverable loss of control over the NFT asset for both the current owner and the denom creator. The NFT remains in state indefinitely with no recovery path.

---

### Likelihood Explanation

The trigger is a completely normal, intended user action: the denom creator minting an NFT and transferring it to another user. This is the primary use case of the NFT module. The spec itself documents that `MsgTransferNFT` sender "should be the Owner of the NFT" with no restriction on who the recipient can be: [7](#0-6) 

Every NFT transferred out of the creator's wallet triggers this condition. Likelihood is **high**.

---

### Recommendation

Decouple the authorization model for `EditNFT` and `BurnNFT`:

- **`EditNFT`**: Allow either the current owner **or** the denom creator (OR logic), not both simultaneously (AND logic).
- **`BurnNFT`**: Allow the current owner alone to burn their NFT. The `IsDenomCreator` check on `BurnNFT` has no justification — ownership is sufficient authorization for destruction.

Alternatively, expose `BurnNFTUnverified` as a separate message restricted to the current owner only.

---

### Proof of Concept

Concrete call sequence on an unmodified Go/Cosmos test setup:

```
1. MsgIssueDenom  { sender=A, id="denom1" }
   → denom.Creator = A (immutable)

2. MsgMintNFT    { sender=A, recipient=A, denomId="denom1", id="token1" }
   → nft.Owner = A

3. MsgTransferNFT { sender=A, recipient=B, denomId="denom1", id="token1" }
   → nft.Owner = B  (only IsOwner checked, passes for A)

4. MsgEditNFT    { sender=A, denomId="denom1", id="token1" }
   → FAILS: IsOwner(A) → A ≠ B → ErrUnauthorized

5. MsgEditNFT    { sender=B, denomId="denom1", id="token1" }
   → PASSES IsOwner(B) → B == B
   → FAILS: IsDenomCreator(B) → B ≠ A → ErrUnauthorized

6. MsgBurnNFT    { sender=A, denomId="denom1", id="token1" }
   → FAILS: IsOwner(A) → A ≠ B → ErrUnauthorized

7. MsgBurnNFT    { sender=B, denomId="denom1", id="token1" }
   → PASSES IsOwner(B) → B == B
   → FAILS: IsDenomCreator(B) → B ≠ A → ErrUnauthorized
```

Steps 4–7 all revert. The NFT at `denom1/token1` is permanently locked with no recovery path.

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

**File:** x/nft/spec/02_messages.md (L31-42)
```markdown
that most chains support the ability to transfer ownership of the non-fungible tokens. The exception to this would be
non-transferable NFTs that might be attached to reputation or some asset which should not be transferable. It still
makes sense for this to be represented as an NFT because there are common queriers which will remain relevant to the NFT
type even if non-transferable. `Sender` of this message should be the `Owner` of the NFT.

| **Field** | **Type** | **Description**                                                                                                  |
| :-------- | :------- | :--------------------------------------------------------------------------------------------------------------- |
| Id        | `string` | The unique ID of the NFT being transferred.                                                                      |
| DenomId   | `string` | The unique ID of the denomination, necessary as multiple denominations are able to be represented on each chain. |
| Sender    | `string` | The account address of the user sending the NFT.                                                                 |
| Recipient | `string` | The account address who will receive the NFT as a result of the transfer transaction.                            |

```
