### Title
Unrestricted NFT Transfer to Blocked Module Accounts Causes Permanent Irrecoverable Lock - (`x/nft/keeper/keeper.go`, `x/nft/keeper/msg_server.go`)

---

### Summary

`TransferOwner` and `MsgTransferNFT` perform no validation that the destination address (`dstOwner` / `recipient`) is not a blocked module account. Because the NFT `Keeper` holds no bank keeper or account keeper reference, there is no `BlockedAddr` guard anywhere in the transfer path. Any NFT owner can irrecoverably lock their NFT by transferring it to a module account address (e.g., `distribution`, `bonded_tokens_pool`), since module accounts cannot sign `MsgTransferNFT` or `MsgBurnNFT`.

---

### Finding Description

**Entry point:** `MsgTransferNFT` → `msgServer.TransferNFT` → `Keeper.TransferOwner`

**`ValidateBasic` on `MsgTransferNFT`** only validates that `Recipient` is a syntactically valid bech32 address: [1](#0-0) 

There is no check that `Recipient` is not a module account.

**`TransferOwner`** only verifies denom existence and that `srcOwner` is the current owner, then unconditionally writes `dstOwner` as the new owner: [2](#0-1) 

**The `Keeper` struct** holds only `storeKey` and `cdc` — no bank keeper or account keeper reference exists to call `BlockedAddr`: [3](#0-2) 

The grep confirms `BlockedAddr` is referenced only in simulation code (`x/nft/simulation/operations.go`) and `expected_keepers.go`, but **never** in the production keeper transfer path.

**`swapOwner`** unconditionally deletes the old owner key and sets the new one with no recipient validation: [4](#0-3) 

**`BurnNFT`** additionally requires `IsDenomCreator`, meaning a non-creator owner who accidentally or intentionally transfers to a module account cannot even burn the NFT to recover the situation: [5](#0-4) 

---

### Impact Explanation

Once an NFT is transferred to a module account address:
- The module account's `GetSigners` is never a valid transaction signer for `MsgTransferNFT` or `MsgBurnNFT`.
- No governance path exists in this module to forcibly reassign ownership.
- The NFT record persists in the KV store permanently with `owner = moduleAccountAddr`, making it irrecoverable.
- The NFT is effectively burned without reducing supply, corrupting the collection's supply invariant.

---

### Likelihood Explanation

The attack requires only that the attacker is the current NFT owner — a standard unprivileged on-chain condition. The module account addresses for `distribution`, `bonded_tokens_pool`, `fee_collector`, etc. are deterministic and publicly known. No special privileges, governance, or operator access are needed. The path is reachable via a single signed `MsgTransferNFT` transaction.

---

### Recommendation

Inject a `BankKeeper` (or `AccountKeeper`) into the NFT `Keeper` and call `BlockedAddr(dstOwner)` at the start of `TransferOwner`, returning an error if the recipient is a blocked module account. The `expected_keepers.go` already defines the interface; the keeper constructor just needs to accept and store the reference.

---

### Proof of Concept

```go
// Integration test sketch
func TestTransferNFTToModuleAccount(t *testing.T) {
    app := simapp.Setup(false)
    ctx := app.BaseApp.NewContext(false, tmproto.Header{})

    // Get distribution module account address (blocked)
    distAddr := app.AccountKeeper.GetModuleAddress(distrtypes.ModuleName)

    // Setup: issue denom and mint NFT to attacker
    attackerAddr := sdk.AccAddress([]byte("attacker__________"))
    app.NFTKeeper.IssueDenom(ctx, "testdenom", "Test", "", "", attackerAddr)
    app.NFTKeeper.MintNFTUnverified(ctx, "testdenom", "token1", "", "", "", attackerAddr)

    // Attack: transfer to distribution module account
    msg := types.NewMsgTransferNFT("token1", "testdenom", attackerAddr.String(), distAddr.String())
    _, err := msgServer.TransferNFT(ctx, msg)

    // Currently: err == nil, NFT is permanently locked
    // Expected:  err != nil (blocked address rejection)
    require.Error(t, err, "transfer to module account should be rejected")
}
```

The transfer currently succeeds (`err == nil`), locking the NFT in the distribution module account with no recovery path.

### Citations

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

**File:** x/nft/keeper/keeper.go (L18-21)
```go
type Keeper struct {
	storeKey storetypes.StoreKey // Unexposed key to access store from sdk.Context
	cdc      codec.Codec
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
