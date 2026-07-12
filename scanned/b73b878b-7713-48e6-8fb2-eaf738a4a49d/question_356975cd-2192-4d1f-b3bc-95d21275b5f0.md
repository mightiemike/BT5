[File: 'x/nft/keeper/collection.go -> Scope: Critical. Inflation, supply, bank, module-account, mint, burn, or escrow accounting flaw creates unbacked assets, loses backed assets, or lets value leave the intended module/account boundary.'] [Function: GetTotalSupply / decreaseSupply] Can a panic or out-of-gas error occurring between deleteNFT and decreaseSupply in BurnNFT (or BurnNFTUnverified) leave the NFT deleted from the KeyNFT store while the supply counter at KeyCollection is not decremented, under preconditions where the Cosmos SDK does not roll back partial state on out-of-gas in a sub-call, via the call sequence BurnNFT -> deleteNFT (NFT removed from store) -> [panic/OOG] -> decreaseSupply never called, violating the invariant GetTotalSupply(denomID) == count(entries in KeyNFT prefix for denomID), causing scoped impact: supply counter overstates actual NFT count, creating phantom supply that persists until the next genesis export/import? Proof idea: inject a panic after deleteNFT but before decreaseSupply in a keeper test and assert GetTotalSupply is greater

### Citations

**File:** x/nft/keeper/collection.go (L120-141)
```go
func (k Keeper) increaseSupply(ctx sdk.Context, denomID string) {
	supply := k.GetTotalSupply(ctx, denomID)
	supply++

	store := ctx.KVStore(k.storeKey)
	bz := types.MustMarshalSupply(k.cdc, supply)
	store.Set(types.KeyCollection(denomID), bz)
}

func (k Keeper) decreaseSupply(ctx sdk.Context, denomID string) {
	supply := k.GetTotalSupply(ctx, denomID)
	supply--

	store := ctx.KVStore(k.storeKey)
	if supply == 0 {
		store.Delete(types.KeyCollection(denomID))
		return
	}

	bz := types.MustMarshalSupply(k.cdc, supply)
	store.Set(types.KeyCollection(denomID), bz)
}
```

**File:** x/nft/keeper/keeper.go (L44-68)
```go
// MintNFTUnverified mints an NFT without verifying if the owner is the creator of denom
// Needed during genesis initialization
func (k Keeper) MintNFTUnverified(ctx sdk.Context, denomID, tokenID, tokenNm, tokenURI, tokenData string, owner sdk.AccAddress) error {
	if !k.HasDenomID(ctx, denomID) {
		return sdkerrors.Wrapf(types.ErrInvalidDenom,
