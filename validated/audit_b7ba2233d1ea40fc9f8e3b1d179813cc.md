### Title
Unbounded `TokenIds` Array Iteration in ICS-721 Packet Lifecycle Causes Permanent NFT Escrow Lock — (`File: x/nft-transfer/keeper/packet.go`)

---

### Summary

The ICS-721 NFT transfer module iterates over an unbounded `data.TokenIds` array in three critical packet-lifecycle functions — `createOutgoingPacket`, `processReceivedPacket`, and `refundPacketToken` — with no enforced maximum on the number of token IDs per packet. A sender can craft a `MsgTransfer` with enough token IDs to exceed the block gas limit in both `OnRecvPacket` and the subsequent `OnTimeoutPacket`/`OnAcknowledgementPacket` refund path, permanently locking the escrowed NFTs with no recovery path.

---

### Finding Description

`MsgTransfer` accepts an arbitrary-length `TokenIds` slice. This slice flows directly into `SendTransfer` → `createOutgoingPacket`, which iterates over every token ID to escrow or burn each NFT: [1](#0-0) 

After the NFTs are escrowed on the source chain, the IBC relayer submits a `RecvPacket` transaction on the destination chain. `OnRecvPacket` calls `processReceivedPacket`, which again iterates over the full unbounded `data.TokenIds` array to mint or unescrow each NFT: [2](#0-1) 

If this loop exhausts the block gas limit, the `RecvPacket` transaction fails. The IBC protocol then writes an error acknowledgement (or the packet times out). Both recovery paths call `refundPacketToken`, which contains the same unbounded loop: [3](#0-2) 

If the refund loop also exceeds the block gas limit — which it will for the same oversized batch — the refund transaction also fails. The NFTs remain permanently locked in the escrow account with no further recovery mechanism in the protocol.

No maximum on `len(TokenIds)` is enforced anywhere in the message validation path. The `Transfer` handler in `msg_server.go` passes `msg.TokenIds` directly to `SendTransfer` without any length check: [4](#0-3) 

---

### Impact Explanation

The corrupted value is the NFT ownership state: NFTs transferred to the IBC escrow address (`types.GetEscrowAddress(sourcePort, sourceChannel)`) during `createOutgoingPacket` can never be recovered. The escrow address holds the NFTs but neither the destination chain can receive them (OOG in `processReceivedPacket`) nor can the source chain refund them (OOG in `refundPacketToken`). The NFT owner permanently loses their assets. [5](#0-4) 

---

### Likelihood Explanation

The attack is reachable by any unprivileged account that owns NFTs in a denom. The attacker simply submits a `MsgTransfer` with a very large `TokenIds` slice. The Cosmos SDK block gas limit is a fixed constant; a sufficiently large batch will always exceed it. The attacker does not need any special role, leaked key, or social engineering — only ownership of enough NFTs (or the ability to mint them in a permissionless denom). [6](#0-5) 

---

### Recommendation

Enforce a maximum number of token IDs per packet in `MsgTransfer.ValidateBasic()` (in `x/nft-transfer/types/msgs.go`). Define a constant such as `MaxTokenIDsPerPacket = 100` and reject any message exceeding it. This mirrors the pattern used in the original report's recommendation to bound `_recipients` to `MAX_ROYALTY_RECIPIENTS_INDEX`. The bound must be set conservatively enough that a full-batch `refundPacketToken` call also fits within the block gas limit. [7](#0-6) 

---

### Proof of Concept

1. Attacker owns (or mints) `N` NFTs in class `classA` on chain A, where `N` is large enough that iterating over all `N` NFTs in a single transaction exceeds the block gas limit.
2. Attacker submits `MsgTransfer{ClassId: "classA", TokenIds: [id_1, ..., id_N], ...}` to chain A.
3. `createOutgoingPacket` iterates over all `N` token IDs and transfers each to the escrow address. This succeeds because the sender pays gas and the loop fits within a single transaction's gas budget (the attacker can tune `N` to be just above the relayer's gas budget but below the sender's).
4. The IBC relayer submits `RecvPacket` on chain B. `processReceivedPacket` iterates over all `N` token IDs. The transaction runs out of gas and fails. An error acknowledgement is written.
5. The relayer submits `AcknowledgePacket` on chain A. `refundPacketToken` iterates over all `N` token IDs. This also runs out of gas and fails.
6. The packet times out. `OnTimeoutPacket` → `refundPacketToken` again iterates over all `N` token IDs and again runs out of gas.
7. The `N` NFTs are permanently locked in the escrow address on chain A with no recovery path. [8](#0-7) [9](#0-8) [10](#0-9)

### Citations

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

**File:** x/nft-transfer/keeper/packet.go (L94-134)
```go
	for _, tokenID := range tokenIDs {
		nft, err := k.nftKeeper.GetNFT(ctx, classID, tokenID)
		if err != nil {
			return channeltypes.Packet{}, err
		}
		tokenURIs = append(tokenURIs, nft.GetURI())

		owner := nft.GetOwner()
		if !sender.Equals(owner) {
			return channeltypes.Packet{}, newsdkerrors.Wrap(sdkerrors.ErrUnauthorized, "not token owner")
		}

		if isAwayFromOrigin {
			// create the escrow address for the tokens
			escrowAddress := types.GetEscrowAddress(sourcePort, sourceChannel)
			if err := k.nftKeeper.TransferOwner(ctx, classID, tokenID, sender, escrowAddress); err != nil {
				return channeltypes.Packet{}, err
			}
		} else {
			// we are sink chain, burn the voucher
			if err := k.nftKeeper.BurnNFTUnverified(ctx, classID, tokenID, sender); err != nil {
				return channeltypes.Packet{}, err
			}
		}
	}

	packetData := types.NewNonFungibleTokenPacketData(
		fullClassPath, denom.Uri, tokenIDs, tokenURIs, sender.String(), receiver,
	)

	return channeltypes.NewPacket(
		packetData.GetBytes(),
		sequence,
		sourcePort,
		sourceChannel,
		destinationPort,
		destinationChannel,
		timeoutHeight,
		timeoutTimestamp,
	), nil
}
```

**File:** x/nft-transfer/keeper/packet.go (L181-201)
```go
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
```

**File:** x/nft-transfer/keeper/msg_server.go (L15-27)
```go
func (k Keeper) Transfer(goCtx context.Context, msg *types.MsgTransfer) (*types.MsgTransferResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	sender, err := sdk.AccAddressFromBech32(msg.Sender)
	if err != nil {
		return nil, err
	}
	if err := k.SendTransfer(
		ctx, msg.SourcePort, msg.SourceChannel, msg.ClassId, msg.TokenIds,
		sender, msg.Receiver, msg.TimeoutHeight, msg.TimeoutTimestamp,
	); err != nil {
		return nil, err
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

**File:** x/nft-transfer/ibc_module.go (L245-271)
```go
// OnTimeoutPacket implements the IBCModule interface
func (im IBCModule) OnTimeoutPacket(
	ctx sdk.Context,
	channelVersion string,
	packet channeltypes.Packet,
	relayer sdk.AccAddress,
) error {
	var data types.NonFungibleTokenPacketData
	if err := types.ModuleCdc.UnmarshalJSON(packet.GetData(), &data); err != nil {
		return newsdkerrors.Wrapf(sdkerrors.ErrUnknownRequest, "cannot unmarshal ICS-721 transfer packet data: %s", err.Error())
	}
	// refund tokens
	if err := im.keeper.OnTimeoutPacket(ctx, channelVersion, packet, data); err != nil {
		return err
	}
	sdkCtx := sdk.UnwrapSDKContext(ctx)
	sdkCtx.EventManager().EmitEvent(
		sdk.NewEvent(
			types.EventTypeTimeout,
			sdk.NewAttribute(sdk.AttributeKeyModule, types.ModuleName),
			sdk.NewAttribute(types.AttributeKeyReceiver, data.Sender),
			sdk.NewAttribute(types.AttributeKeyClassID, data.ClassId),
			sdk.NewAttribute(types.AttributeKeyTokenIDs, strings.Join(data.TokenIds, ",")),
		),
	)

	return nil
```
