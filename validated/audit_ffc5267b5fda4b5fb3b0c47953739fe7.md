### Title
ICS-721 Packet `ValidateBasic` Enforces Bech32 Address Format in Violation of the Standard, Blocking Cross-Chain NFT Transfers - (File: x/nft-transfer/types/packet.go)

### Summary

The `ValidateBasic` function in `x/nft-transfer/types/packet.go` validates both the `Sender` and `Receiver` fields of `NonFungibleTokenPacketData` as Cosmos bech32 addresses. This directly contradicts the ICS-721 specification and the code's own inline comment, which explicitly states that address formats must not be validated because counterparty chains may use different formats. As a result, any IBC NFT packet arriving from a non-Cosmos chain (e.g., one using Ethereum hex addresses) will be rejected by `OnRecvPacket` with an error acknowledgement, permanently blocking cross-chain NFT transfers from such chains.

### Finding Description

The `ValidateBasic` function carries this comment:

> NOTE: The addresses formats are not validated as the sender and recipient can have different formats defined by their corresponding chains that are not known to IBC.

Despite this, the implementation immediately below it performs bech32 validation on both addresses:

```go
// decode the sender address
if _, err := sdk.AccAddressFromBech32(nftpd.Sender); err != nil {
    return newsdkerrors.Wrap(sdkerrors.ErrInvalidAddress, "invalid sender address")
}
// ...
// decode the receiver address
if _, err := sdk.AccAddressFromBech32(nftpd.Receiver); err != nil {
    return newsdkerrors.Wrap(sdkerrors.ErrInvalidAddress, "invalid receiver address")
}
``` [1](#0-0) 

This `ValidateBasic` is called unconditionally inside `keeper.OnRecvPacket` before any application logic runs:

```go
func (k Keeper) OnRecvPacket(...) error {
    if err := data.ValidateBasic(); err != nil {
        return err
    }
    return k.processReceivedPacket(ctx, packet, data)
}
``` [2](#0-1) 

`keeper.OnRecvPacket` is invoked from `IBCModule.OnRecvPacket`, which converts any returned error into an error acknowledgement written on-chain: [3](#0-2) 

The `Sender` field in the packet is the address on the **source chain**. When the source chain is non-Cosmos (e.g., Ethereum, Solana), its address format is not bech32. `sdk.AccAddressFromBech32` will return an error, `ValidateBasic` will fail, and `OnRecvPacket` will write an error acknowledgement. The NFT is then permanently stuck: if the source chain escrowed it (away-from-origin direction), it remains locked in escrow; if it was burned (sink direction), it is lost.

The `processReceivedPacket` function only uses `data.Receiver` (not `data.Sender`) to credit the NFT, so the sender validation serves no functional purpose and is purely a standard violation: [4](#0-3) 

The `NonFungibleTokenPacketData` proto definition references the ICS-721 spec, which mandates that address formats are chain-specific and must not be validated by the receiving module: [5](#0-4) 

### Impact Explanation

Any IBC NFT transfer originating from a non-Cosmos chain whose address format is not bech32 will be permanently rejected by this chain's `OnRecvPacket`. The error acknowledgement triggers `OnAcknowledgementPacket` on the source chain, which calls `refundPacketToken`. If the source chain is away-from-origin, the NFT is unescrow-refunded to the sender. If the source chain is a sink, `MintNFT` is called to re-mint the voucher. In either case, the cross-chain transfer is permanently broken for all non-Cosmos counterparties — NFT ownership is never transferred to the intended receiver on this chain, and the ICS-721 channel is rendered non-functional for those peers. [6](#0-5) 

### Likelihood Explanation

The Cronos ecosystem explicitly targets Ethereum-compatible chains. An IBC NFT channel opened between this chain and any EVM chain (whose addresses are `0x`-prefixed hex strings) will trigger this failure on every single inbound packet. The entry path requires only a standard IBC relayer submitting a valid ICS-721 packet — no privileged access is needed.

### Recommendation

Remove the bech32 address validation for both `Sender` and `Receiver` from `NonFungibleTokenPacketData.ValidateBasic`, consistent with the ICS-721 specification and the comment already present in the function. The receiver address is parsed again inside `processReceivedPacket` via `sdk.AccAddressFromBech32(data.Receiver)`, which is the correct place to enforce the local chain's address format.

### Proof of Concept

1. Chain A (Ethereum-compatible, addresses like `0x1234...`) opens an ICS-721 channel to this chain.
2. User on Chain A calls the NFT transfer entrypoint; the relayer submits the IBC packet with `Sender = "0x1234abcd..."`.
3. This chain's `IBCModule.OnRecvPacket` is called.
4. `keeper.OnRecvPacket` calls `data.ValidateBasic()`.
5. `sdk.AccAddressFromBech32("0x1234abcd...")` returns an error (not a valid bech32 string).
6. `ValidateBasic` returns `"invalid sender address"`.
7. `OnRecvPacket` sets `ack = types.NewErrorAcknowledgement(err)` and returns it.
8. The relayer writes the error acknowledgement; `OnAcknowledgementPacket` on Chain A refunds the sender.
9. The NFT never arrives on this chain. Every subsequent attempt produces the same result. [7](#0-6) [2](#0-1) [8](#0-7)

### Citations

**File:** x/nft-transfer/types/packet.go (L38-71)
```go
// ValidateBasic is used for validating the nft transfer.
// NOTE: The addresses formats are not validated as the sender and recipient can have different
// formats defined by their corresponding chains that are not known to IBC.
func (nftpd NonFungibleTokenPacketData) ValidateBasic() error {
	if strings.TrimSpace(nftpd.ClassId) == "" {
		return newsdkerrors.Wrap(ErrInvalidClassID, "classId cannot be blank")
	}

	if len(nftpd.TokenIds) == 0 {
		return newsdkerrors.Wrap(ErrInvalidTokenID, "tokenId cannot be blank")
	}

	if len(nftpd.TokenIds) != len(nftpd.TokenUris) {
		return newsdkerrors.Wrap(ErrInvalidPacket, "tokenIds and tokenUris lengths do not match")
	}

	if strings.TrimSpace(nftpd.Sender) == "" {
		return newsdkerrors.Wrap(sdkerrors.ErrInvalidAddress, "sender address cannot be blank")
	}

	// decode the sender address
	if _, err := sdk.AccAddressFromBech32(nftpd.Sender); err != nil {
		return newsdkerrors.Wrap(sdkerrors.ErrInvalidAddress, "invalid sender address")
	}

	if strings.TrimSpace(nftpd.Receiver) == "" {
		return newsdkerrors.Wrap(sdkerrors.ErrInvalidAddress, "receiver address cannot be blank")
	}

	// decode the receiver address
	if _, err := sdk.AccAddressFromBech32(nftpd.Receiver); err != nil {
		return newsdkerrors.Wrap(sdkerrors.ErrInvalidAddress, "invalid receiver address")
	}
	return nil
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

**File:** x/nft-transfer/ibc_module.go (L153-188)
```go
func (im IBCModule) OnRecvPacket(
	ctx sdk.Context,
	channelVersion string,
	packet channeltypes.Packet,
	relayer sdk.AccAddress,
) ibcexported.Acknowledgement {
	ack := channeltypes.NewResultAcknowledgement([]byte{byte(1)})

	var data types.NonFungibleTokenPacketData
	if err := types.ModuleCdc.UnmarshalJSON(packet.GetData(), &data); err != nil {
		ack = channeltypes.NewErrorAcknowledgement(fmt.Errorf("cannot unmarshal ICS-721 nft-transfer packet data"))
	}

	// only attempt the application logic if the packet data
	// was successfully decoded
	if ack.Success() {
		if err := im.keeper.OnRecvPacket(ctx, channelVersion, packet, data); err != nil {
			ack = types.NewErrorAcknowledgement(err)
		}
	}
	sdkCtx := sdk.UnwrapSDKContext(ctx)
	sdkCtx.EventManager().EmitEvent(
		sdk.NewEvent(
			types.EventTypePacket,
			sdk.NewAttribute(sdk.AttributeKeyModule, types.ModuleName),
			sdk.NewAttribute(sdk.AttributeKeySender, data.Sender),
			sdk.NewAttribute(types.AttributeKeyReceiver, data.Receiver),
			sdk.NewAttribute(types.AttributeKeyClassID, data.ClassId),
			sdk.NewAttribute(types.AttributeKeyTokenIDs, strings.Join(data.TokenIds, ",")),
			sdk.NewAttribute(types.AttributeKeyAckSuccess, fmt.Sprintf("%t", ack.Success())),
		),
	)

	// NOTE: acknowledgement will be written synchronously during IBC handler execution.
	return ack
}
```

**File:** x/nft-transfer/keeper/packet.go (L21-51)
```go
func (k Keeper) refundPacketToken(ctx sdk.Context, packet channeltypes.Packet, data types.NonFungibleTokenPacketData) error {
	sender, err := sdk.AccAddressFromBech32(data.Sender)
	if err != nil {
		return err
	}

	classTrace := types.ParseClassTrace(data.ClassId)
	voucherClassID := classTrace.IBCClassID()

	isAwayFromOrigin := types.IsAwayFromOrigin(packet.GetSourcePort(),
		packet.GetSourceChannel(), data.ClassId)

	escrowAddress := types.GetEscrowAddress(packet.GetSourcePort(), packet.GetSourceChannel())

	if isAwayFromOrigin {
		// unescrow tokens back to the sender
		for _, tokenID := range data.TokenIds {
			if err := k.nftKeeper.TransferOwner(ctx, voucherClassID, tokenID, escrowAddress, sender); err != nil {
				return err
			}
		}
	} else {
		// we are sink chain, mint voucher back to sender
		for i, tokenID := range data.TokenIds {
			if err := k.nftKeeper.MintNFT(ctx, voucherClassID, tokenID, "", data.TokenUris[i], "", escrowAddress, sender); err != nil {
				return err
			}
		}
	}

	return nil
```

**File:** x/nft-transfer/keeper/packet.go (L140-205)
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
}
```

**File:** proto/chainmain/nft_transfer/v1/packet.proto (L6-22)
```text
// NonFungibleTokenPacketData defines a struct for the packet payload
// See NonFungibleTokenPacketData spec:
// https://github.com/cosmos/ibc/tree/master/spec/app/ics-721-nft-transfer#data-structures
message NonFungibleTokenPacketData {
  // the class_id of tokens to be transferred
  string class_id = 1;
  // the class_uri of tokens to be transferred
  string class_uri = 2;
  // the non fungible tokens to be transferred (count should be equal to token_uris)
  repeated string token_ids = 3;
  // the non fungible tokens's uri to be transferred (count should be equal to token ids)
  repeated string token_uris = 4;
  // the sender address
  string sender = 5;
  // the recipient address on the destination chain
  string receiver = 6;
}
```
