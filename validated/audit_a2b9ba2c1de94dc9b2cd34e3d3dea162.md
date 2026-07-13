### Title
Missing Blocked-Address Guard in `TransferOwner` Permanently Locks NFTs in Module Accounts with Inflated Supply Counter — (`x/nft/keeper/keeper.go`)

---

### Summary

`TransferOwner` accepts any valid bech32 address as `dstOwner` without checking whether it is a blocked module account. An NFT owner can send `MsgTransferNFT` with `Recipient` set to a module account address (e.g., `distribution`, `bonded_tokens_pool`). The transfer succeeds, the NFT's owner record is updated to the module account, and the supply counter is never decremented — because no external signer can ever satisfy the `IsOwner` check required by `BurnNFT` or a subsequent `TransferOwner`.

---

### Finding Description

**Entrypoint — `msgServer.TransferNFT`**

`TransferNFT` decodes both `Sender` and `Recipient` as bech32 addresses and immediately calls `TransferOwner`. No blocked-address check is performed at any layer. [1](#0-0) 

**Keeper — `TransferOwner`**

`TransferOwner` performs exactly two guards: `HasDenomID` (denom exists) and `IsOwner` (sender is current owner). It then calls `setNFT` and `swapOwner` unconditionally. There is no call to any bank keeper `BlockedAddr` equivalent, and the `Keeper` struct holds no bank keeper reference at all. [2](#0-1) 

**`swapOwner` — state mutation**

`swapOwner` deletes the old owner key and writes the new one. After this call the module account address is the canonical owner in the KV store. [3](#0-2) 

**`ValidateBasic` — no blocked-address check**

`MsgTransferNFT.ValidateBasic` only validates that `Recipient` is a parseable bech32 address. It does not consult `BlockedAddrs`. [4](#0-3) 

**`BankKeeper` interface — `BlockedAddr` absent**

The `BankKeeper` interface exposed to the NFT module does not include `BlockedAddr`, so even if the keeper wanted to check, it has no mechanism to do so. [5](#0-4) 

**Why the NFT is permanently locked**

`BurnNFT` requires the caller to satisfy both `IsOwner` and `IsDenomCreator`. Module accounts have no private keys; no external signer can produce a transaction signed by the module account address. Therefore neither `BurnNFT` nor a subsequent `TransferOwner` can ever be executed for this NFT. [6](#0-5) 

**Supply counter permanently inflated**

`decreaseSupply` is called only inside `BurnNFT` and `BurnNFTUnverified`. Since the NFT can never be burned, `decreaseSupply` is never called, and `GetTotalSupply` for that denom returns a count that is permanently higher than the number of accessible NFTs. [7](#0-6) [8](#0-7) 

---

### Impact Explanation

- The NFT is permanently inaccessible: no transfer, edit, or burn is possible.
- The denom's supply counter is permanently inflated by 1 per locked NFT, diverging from the true count of accessible tokens. Any on-chain or off-chain logic that relies on `GetTotalSupply` (e.g., IBC class-trace supply invariants, marketplace integrations) will observe an incorrect value.
- Value (the NFT asset) exits the intended owner/user boundary and enters a module account that cannot act as a signer, constituting a permanent loss of that asset.

---

### Likelihood Explanation

The path requires only a standard `MsgTransferNFT` transaction signed by the NFT's legitimate owner, with `Recipient` set to any known module account address (all of which are publicly derivable via `authtypes.NewModuleAddress`). No special privileges, governance, or operator access are needed. The transaction passes `ValidateBasic`, `AnteHandler` signature verification, and all keeper guards without error.

---

### Recommendation

1. Add `BlockedAddr` to the `BankKeeper` interface in `x/nft/types/expected_keepers.go`.
2. In `TransferOwner` (or in `msgServer.TransferNFT` before calling it), reject the transfer if `bankKeeper.BlockedAddr(dstOwner)` returns `true`, returning an appropriate error.
3. Optionally add the same guard to `MintNFT` / `MintNFTUnverified` for the `owner` (recipient) parameter.

---

### Proof of Concept

```go
// keeper_test.go (illustrative)
func TestTransferNFTToModuleAccount(t *testing.T) {
    ctx, keeper := setupKeeper(t)
    owner := sdk.AccAddress([]byte("owner"))
    distAddr := authtypes.NewModuleAddress("distribution")

    // Setup: issue denom and mint NFT to owner
    _ = keeper.IssueDenom(ctx, "testdenom", "Test", "", "", owner)
    _ = keeper.MintNFT(ctx, "testdenom", "token1", "", "", "", owner, owner)

    // Attack: transfer to blocked module account — succeeds with no error
    err := keeper.TransferOwner(ctx, "testdenom", "token1", owner, distAddr)
    require.NoError(t, err) // passes — no blocked-addr check

    // NFT owner is now the module account
    nft, _ := keeper.GetNFT(ctx, "testdenom", "token1")
    require.Equal(t, distAddr.String(), nft.GetOwner().String())

    // Supply counter is still 1 (not decremented)
    require.Equal(t, uint64(1), keeper.GetTotalSupply(ctx, "testdenom"))

    // No one can burn or transfer — module account has no signer
    err = keeper.BurnNFT(ctx, "testdenom", "token1", distAddr)
    require.Error(t, err) // fails: no tx can be signed by distAddr
}
```

### Citations

**File:** x/nft/keeper/msg_server.go (L129-146)
```go
func (m msgServer) TransferNFT(goCtx context.Context, msg *types.MsgTransferNFT) (*types.MsgTransferNFTResponse, error) {
	sender, err := sdk.AccAddressFromBech32(msg.Sender)
	if err != nil {
		return nil, err
	}

	recipient, err := sdk.AccAddressFromBech32(msg.Recipient)
	if err != nil {
		return nil, err
	}

	ctx := sdk.UnwrapSDKContext(goCtx)
	if err := m.TransferOwner(ctx, msg.DenomId, msg.Id,
		sender,
		recipient,
	); err != nil {
		return nil, err
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

**File:** x/nft/keeper/owners.go (L95-101)
```go
func (k Keeper) swapOwner(ctx sdk.Context, denomID, tokenID string, srcOwner, dstOwner sdk.AccAddress) {
	// delete old owner key
	k.deleteOwner(ctx, denomID, tokenID, srcOwner)

	// set new owner key
	k.setOwner(ctx, denomID, tokenID, dstOwner)
}
```

**File:** x/nft/types/msgs.go (L85-98)
```go
func (msg MsgTransferNFT) ValidateBasic() error {
	if err := ValidateDenomID(msg.DenomId); err != nil {
		return err
	}

	if _, err := sdk.AccAddressFromBech32(msg.Sender); err != nil {
		return newsdkerrors.Wrapf(sdkerrors.ErrInvalidAddress, "invalid sender address (%s)", err)
	}

	if _, err := sdk.AccAddressFromBech32(msg.Recipient); err != nil {
		return newsdkerrors.Wrapf(sdkerrors.ErrInvalidAddress, "invalid recipient address (%s)", err)
	}
	return ValidateTokenID(msg.Id)
}
```

**File:** x/nft/types/expected_keepers.go (L16-21)
```go
type BankKeeper interface {
	GetAllBalances(ctx context.Context, addr sdk.AccAddress) sdk.Coins
	GetBalance(ctx context.Context, addr sdk.AccAddress, denom string) sdk.Coin
	LockedCoins(ctx context.Context, addr sdk.AccAddress) sdk.Coins
	SpendableCoins(ctx context.Context, addr sdk.AccAddress) sdk.Coins
}
```

**File:** x/nft/keeper/collection.go (L129-141)
```go
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
