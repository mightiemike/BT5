The `BurnNFT` keeper function has a concrete, testable authorization logic flaw. Here is the exact analysis:

---

### Title
NFT Permanently Unburnable When Creator Mints to Non-Creator Recipient — (`x/nft/keeper/keeper.go`)

### Summary
`keeper.BurnNFT` requires the caller to simultaneously satisfy **both** `IsOwner` (caller == NFT owner) **and** `IsDenomCreator` (caller == denom creator). When a denom creator mints an NFT to a different recipient, neither the creator nor the recipient can ever satisfy both conditions at once, making the NFT permanently unburnable by any unprivileged account.

### Finding Description

`keeper.BurnNFT` enforces two sequential AND-gated checks: [1](#0-0) 

Line 146 calls `IsOwner`, which returns `ErrUnauthorized` if `owner != nft.GetOwner()`: [2](#0-1) 

Line 151 then calls `IsDenomCreator`, which returns `ErrUnauthorized` if `address != denom.Creator`: [3](#0-2) 

The two checks are mutually exclusive in the scenario where `creator != owner`:

| Caller | `IsOwner` | `IsDenomCreator` | Result |
|---|---|---|---|
| bob (owner, non-creator) | PASS | FAIL | `ErrUnauthorized` |
| alice (creator, non-owner) | FAIL | — | `ErrUnauthorized` |

`MintNFT` explicitly supports minting to a different recipient: [4](#0-3) 

Once `alice` mints to `bob`, the NFT is permanently unburnable. There is no admin override, no governance path, and no `BurnNFTUnverified` variant exposed to users (it exists only for IBC use): [5](#0-4) 

### Impact Explanation
Any NFT minted by a denom creator to a different recipient address is permanently unburnable by any unprivileged account. The NFT remains in on-chain state indefinitely with no destruction path. The owner retains transfer rights but loses the ability to destroy the asset. This is a permanent functional lock on the burn operation for a class of legitimately issued NFTs.

### Likelihood Explanation
The `MintNFT` CLI explicitly supports a `--recipient` flag for minting to a third party: [6](#0-5) 

This is a standard, documented, production workflow. Any creator who uses `--recipient` to mint to a user triggers the broken invariant. The scenario is not adversarial — it is the normal intended use of the mint-to-recipient feature.

### Recommendation
`BurnNFT` should require **either** `IsOwner` **or** `IsDenomCreator`, not both. The correct authorization model is: the current NFT owner may burn their own NFT. Optionally, the denom creator may also be permitted to burn any NFT in their denom. The AND-gate must be replaced with an OR-gate, or the check should be ownership-only (matching how `TransferOwner` works).

### Proof of Concept

```go
// keeper integration test
IssueDenom(ctx, "denomA", alice)
MintNFT(ctx, "denomA", "token1", alice /*sender*/, bob /*recipient*/)

err := BurnNFT(ctx, "denomA", "token1", bob)
// bob is owner but not creator → ErrUnauthorized ✓

err = BurnNFT(ctx, "denomA", "token1", alice)
// alice is creator but not owner → ErrUnauthorized ✓

// NFT still exists — permanently unburnable by any unprivileged account
assert HasNFT(ctx, "denomA", "token1") == true
``` [1](#0-0) 

#Vulnerability found.

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

**File:** x/nft/client/cli/tx.go (L116-128)
```go
			recipient, err := cmd.Flags().GetString(FlagRecipient)
			if err != nil {
				return err
			}

			recipientStr := strings.TrimSpace(recipient)
			if len(recipientStr) > 0 {
				if _, err = sdk.AccAddressFromBech32(recipientStr); err != nil {
					return err
				}
			} else {
				recipient = sender
			}
```
