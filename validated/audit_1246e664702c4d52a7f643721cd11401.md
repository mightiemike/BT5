### Title
Missing Blocked-Address Guard in `processReceivedPacket` Allows Permanent NFT Lock in Module Accounts - (`x/nft-transfer/keeper/packet.go`)

### Summary

The `processReceivedPacket` function in the IBC NFT-transfer keeper accepts any valid bech32 address as the receiver, including addresses of blocked module accounts (e.g., `distribution`, `bonded_tokens_pool`). There is no equivalent of the bank keeper's `BlockedAddr` check anywhere in the nft-transfer module. An NFT minted to a module account is permanently irrecoverable because module accounts cannot sign transactions to transfer it out.

### Finding Description

`OnRecvPacket` calls `data.ValidateBasic()` then `processReceivedPacket`. `ValidateBasic` only verifies the receiver is a non-empty, parseable bech32 address: [1](#0-0) 

`processReceivedPacket` then decodes the receiver and passes it directly to `MintNFT` (mint path) or `TransferOwner` (unescrow path) with no further validation: [2](#0-1) [3](#0-2) [4](#0-3) 

The nft-transfer `Keeper` struct holds no `bankKeeper` and no blocked-address map: [5](#0-4) 

The `NFTKeeper.MintNFT` implementation only checks denom creator authorization and NFT existence — no blocked-address check: [6](#0-5) 

The application does define `BlockedAddrs()` and uses it for the bank keeper, but this map is never wired into the nft-transfer keeper: [7](#0-6) 

### Impact Explanation

When a counterparty chain sends an IBC NFT packet with `receiver` set to the bech32 address of a blocked module account (e.g., `cosmos1jv65s3grqf6v6jl3dp4t6c9t9rk99cd88lyufl` for `distribution`):

1. `OnRecvPacket` → `processReceivedPacket` succeeds — the address is valid bech32.
2. `MintNFT` records the module account as the NFT owner in the NFT store.
3. No module-level code ever calls `TransferOwner` on behalf of that module account for arbitrary NFTs.
4. No external signer can produce a valid `MsgTransferNFT` signed by the module account.
5. The NFT is permanently locked. The source-chain NFT was already burned or escrowed, so there is no refund path (the ack is a success).

### Likelihood Explanation

Module account bech32 addresses are deterministic and publicly known. Any user on a counterparty chain can craft a `MsgTransfer` (nft-transfer) with `receiver` set to one of these addresses. No special privilege is required — it is a standard unprivileged IBC send. The packet will be relayed and accepted by `OnRecvPacket` without error, producing a success acknowledgement and permanently locking the NFT.

### Recommendation

Add a blocked-address interface to `NFTKeeper` or introduce a `BankKeeper` interface in `x/nft-transfer/types/expected_keepers.go` exposing `BlockedAddr(addr sdk.AccAddress) bool`. In `processReceivedPacket`, after decoding the receiver, reject the packet if the receiver is a blocked address:

```go
receiver, err := sdk.AccAddressFromBech32(data.Receiver)
if err != nil {
    return err
}
if k.bankKeeper.BlockedAddr(receiver) {
    return sdkerrors.Wrapf(sdkerrors.ErrUnauthorized,
        "%s is a blocked address and cannot receive NFTs", data.Receiver)
}
```

This mirrors the guard present in the ICS-20 fungible token transfer module.

### Proof of Concept

1. Compute the bech32 address of the `distribution` module account:
   ```
   addr = sdk.AccAddress(crypto.AddressHash([]byte("distribution")))
   // e.g. cro1jv65s3grqf6v6jl3dp4t6c9t9rk99cd8lyufl (chain prefix)
   ```
2. On the counterparty chain, call `MsgTransfer` (nft-transfer) with `receiver = <distribution bech32>` and a valid NFT.
3. The relayer submits the packet; `OnRecvPacket` is called on Cronos POS Chain.
4. `processReceivedPacket` parses the address successfully, calls `MintNFT(..., receiver)`.
5. Query the NFT owner — it is the `distribution` module account address.
6. Attempt `MsgTransferNFT` signed by any key: fails with "not token owner".
7. No governance or module-level mechanism exists to recover the NFT. The source-chain NFT is gone (burned/escrowed). The NFT is permanently locked. [8](#0-7) [9](#0-8)

### Citations

**File:** x/nft-transfer/types/packet.go (L63-70)
```go
	if strings.TrimSpace(nftpd.Receiver) == "" {
		return newsdkerrors.Wrap(sdkerrors.ErrInvalidAddress, "receiver address cannot be blank")
	}

	// decode the receiver address
	if _, err := sdk.AccAddressFromBech32(nftpd.Receiver); err != nil {
		return newsdkerrors.Wrap(sdkerrors.ErrInvalidAddress, "invalid receiver address")
	}
```

**File:** x/nft-transfer/keeper/packet.go (L140-204)
```go
func (k Keeper) processReceivedPacket(ctx sdk.Context, packet channeltypes.Packet,
	data types.NonFungibleTokenPacketData,
) error {
	receiver, err := sdk.AccAddressFromBech32(data.Receiver)
	if err != nil {
		return err
	}

	isAwayFromOrigin := types.IsAwayFromOrigin(packet.GetSourcePort(), packet.GetSourceChannel(), data.ClassId)

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
	} else {
		// If the token moves in the direction of back to origin,
		// we need to unescrow the token and transfer it to the receiver

		// we should remove the prefix. For example:
		// p6/c6/p4/c4/p2/c2/nftClass -> p4/c4/p2/c2/nftClass
		unprefixedClassID := types.RemoveClassPrefix(packet.GetSourcePort(),
			packet.GetSourceChannel(), data.ClassId)

		voucherClassID := types.ParseClassTrace(unprefixedClassID).IBCClassID()
		for _, tokenID := range data.TokenIds {
			if err := k.nftKeeper.TransferOwner(ctx,
				voucherClassID, tokenID, escrowAddress, receiver); err != nil {
				return err
			}
		}
	}

	return nil
```

**File:** x/nft-transfer/keeper/keeper.go (L15-23)
```go
type Keeper struct {
	storeKey storetypes.StoreKey
	cdc      codec.BinaryCodec

	ics4Wrapper   types.ICS4Wrapper
	channelKeeper types.ChannelKeeper
	nftKeeper     types.NFTKeeper
	authKeeper    types.AccountKeeper
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

**File:** app/app.go (L853-862)
```go
// BlockedAddrs returns all the app's module account addresses that are not
// allowed to receive external tokens.
func (app *ChainApp) BlockedAddrs() map[string]bool {
	blockedAddrs := make(map[string]bool)
	for acc := range maccPerms {
		blockedAddrs[authtypes.NewModuleAddress(acc).String()] = !moduleAccsAllowedToReceiveExternalFunds[acc]
	}

	return blockedAddrs
}
```

**File:** x/nft-transfer/keeper/relay.go (L101-111)
```go
func (k Keeper) OnRecvPacket(ctx sdk.Context, channelVersion string, packet channeltypes.Packet,
	data types.NonFungibleTokenPacketData,
) error {
	// validate packet data upon receiving
	if err := data.ValidateBasic(); err != nil {
		return err
	}

	// See spec for this logic: https://github.com/cosmos/ibc/blob/master/spec/app/ics-721-nft-transfer/README.md#packet-relay
	return k.processReceivedPacket(ctx, packet, data)
}
```
