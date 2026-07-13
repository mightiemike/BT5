### Title
Missing Counterparty Port Validation in ICS-721 Channel Handshake Allows Unauthorized NFT Minting and Unescrow - (File: x/nft-transfer/ibc_module.go)

---

### Summary

The `x/nft-transfer` module's `OnChanOpenInit` and `OnChanOpenTry` handlers never validate that the counterparty's port ID equals `types.PortID` (`"nft-transfer"`). A malicious chain can therefore open a channel from an arbitrary port to the local `nft-transfer` port, and subsequently send crafted ICS-721 packets that trigger unbacked NFT voucher minting or unauthorized unescrow of held NFTs.

---

### Finding Description

`ValidateTransferChannelParams` — the shared helper called by both channel-open callbacks — checks only channel ordering and sequence bounds. It never inspects the counterparty port. [1](#0-0) 

`OnChanOpenInit` calls `ValidateTransferChannelParams`, checks the local version string, and returns — the `counterparty.PortId` field is never read. [2](#0-1) 

`OnChanOpenTry` does the same: it validates the counterparty *version* but not the counterparty *port*. [3](#0-2) 

Once the channel is open, `OnRecvPacket` decodes the packet and immediately delegates to `keeper.OnRecvPacket` with no additional source-port check. [4](#0-3) 

`keeper.OnRecvPacket` calls `processReceivedPacket`, which branches on `IsAwayFromOrigin` using the packet's `SourcePort`/`SourceChannel` fields. When the path is "away from origin," it mints new NFT vouchers; when it is "back to origin," it unescrows held NFTs — all without any check that the source port is `nft-transfer`. [5](#0-4) 

The reference ICS-20 implementation in ibc-go explicitly rejects channels whose counterparty port is not `transfer`. No equivalent guard exists here.

---

### Impact Explanation

**Unbacked minting (away-from-origin path):** A malicious chain opens a channel from port `"evil"` to the local `nft-transfer` port. It then sends a packet whose `ClassId` does not carry the `"evil/<channel>/"` prefix, so `IsAwayFromOrigin` returns `true`. `processReceivedPacket` mints an arbitrary number of IBC-voucher NFTs (`ibc/<hash>`) to any receiver address the attacker specifies, with no corresponding escrow on any legitimate chain. [6](#0-5) 

**Unauthorized unescrow (back-to-origin path):** If the attacker crafts a `ClassId` that begins with the local destination port/channel prefix, `IsAwayFromOrigin` returns `false`. `processReceivedPacket` calls `TransferOwner` from the escrow address to the attacker-controlled receiver, draining legitimately escrowed NFTs. [7](#0-6) 

The corrupted values are: the NFT owner field for escrowed tokens (drained from the escrow account) and the total supply of IBC-voucher NFT classes (inflated without backing).

---

### Likelihood Explanation

IBC is permissionless at the relayer layer. Any chain with an IBC light client can initiate a `ChanOpenInit` / `ChanOpenTry` handshake. The attacker needs only to operate or compromise a chain that can submit IBC transactions, which is a realistic threat in a multi-chain ecosystem. No privileged key or governance action on the victim chain is required; the handshake succeeds purely because the application-layer port check is absent.

---

### Recommendation

Add a counterparty port check in both `OnChanOpenInit` and `OnChanOpenTry`:

```go
// OnChanOpenInit
if counterparty.PortId != types.PortID {
    return "", sdkerrors.Wrapf(porttypes.ErrInvalidPort,
        "invalid counterparty port: %s, expected %s",
        counterparty.PortId, types.PortID)
}

// OnChanOpenTry  (same guard, same location)
if counterparty.PortId != types.PortID {
    return "", sdkerrors.Wrapf(porttypes.ErrInvalidPort,
        "invalid counterparty port: %s, expected %s",
        counterparty.PortId, types.PortID)
}
```

This mirrors the guard present in the canonical ICS-20 `transfer` module and ensures that only peer chains running the `nft-transfer` application can exchange ICS-721 packets with this module.

---

### Proof of Concept

1. **Attacker operates chain B** with a custom IBC module bound to port `"evil"`.
2. **Chain B submits `MsgChannelOpenInit`** targeting chain A's `nft-transfer` port, with `counterparty.PortId = "evil"` and `version = "ics721-1"`.
3. **Chain A's `OnChanOpenTry`** is invoked. `ValidateTransferChannelParams` passes (ordering and sequence are valid). The counterparty version `"ics721-1"` matches `types.Version`. No port check exists → channel is accepted. [3](#0-2) 
4. **Chain B commits a packet** with:
   - `SourcePort = "evil"`, `SourceChannel = "channel-0"`
   - `data.ClassId = "nftClass"` (no `"evil/channel-0/"` prefix → `IsAwayFromOrigin = true`)
   - `data.TokenIds = ["token1"]`, `data.TokenUris = ["uri1"]`
   - `data.Receiver = <attacker address on chain A>`
5. **Relayer delivers the packet** to chain A. `OnRecvPacket` decodes it successfully and calls `keeper.OnRecvPacket`. [4](#0-3) 
6. **`processReceivedPacket`** takes the `isAwayFromOrigin = true` branch, creates a new denom `ibc/<hash of nft-transfer/channel-X/nftClass>`, and mints `token1` to the attacker's address — with zero NFTs ever escrowed on any legitimate chain. [6](#0-5)

### Citations

**File:** x/nft-transfer/ibc_module.go (L37-58)
```go
func ValidateTransferChannelParams(
	ctx sdk.Context,
	keeper keeper.Keeper,
	order channeltypes.Order,
	portID string,
	channelID string,
) error {
	// NOTE: for escrow address security only 2^32 channels are allowed to be created
	// Issue: https://github.com/cosmos/cosmos-sdk/issues/7737
	channelSequence, err := channeltypes.ParseChannelSequence(channelID)
	if err != nil {
		return err
	}
	if channelSequence > uint64(math.MaxUint32) {
		return newsdkerrors.Wrapf(types.ErrMaxTransferChannels, "channel sequence %d is greater than max allowed nft-transfer channels %d", channelSequence, uint64(math.MaxUint32))
	}
	if order != channeltypes.UNORDERED {
		return newsdkerrors.Wrapf(channeltypes.ErrInvalidChannelOrdering, "expected %s channel, got %s ", channeltypes.UNORDERED, order)
	}

	return nil
}
```

**File:** x/nft-transfer/ibc_module.go (L61-83)
```go
func (im IBCModule) OnChanOpenInit(
	ctx sdk.Context,
	order channeltypes.Order,
	connectionHops []string,
	portID string,
	channelID string,
	counterparty channeltypes.Counterparty,
	version string,
) (string, error) {
	if err := ValidateTransferChannelParams(ctx, im.keeper, order, portID, channelID); err != nil {
		return "", err
	}

	if strings.TrimSpace(version) == "" {
		version = types.Version
	}

	if version != types.Version {
		return "", newsdkerrors.Wrapf(types.ErrInvalidVersion, "got %s, expected %s", version, types.Version)
	}

	return version, nil
}
```

**File:** x/nft-transfer/ibc_module.go (L86-104)
```go
func (im IBCModule) OnChanOpenTry(
	ctx sdk.Context,
	order channeltypes.Order,
	connectionHops []string,
	portID,
	channelID string,
	counterparty channeltypes.Counterparty,
	counterpartyVersion string,
) (string, error) {
	if err := ValidateTransferChannelParams(ctx, im.keeper, order, portID, channelID); err != nil {
		return "", err
	}

	if counterpartyVersion != types.Version {
		return "", newsdkerrors.Wrapf(types.ErrInvalidVersion, "invalid counterparty version: %s, expected %s", counterpartyVersion, types.Version)
	}

	return types.Version, nil
}
```

**File:** x/nft-transfer/ibc_module.go (L153-172)
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
```

**File:** x/nft-transfer/keeper/packet.go (L140-202)
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
```
