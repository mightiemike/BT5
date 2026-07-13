### Title
IBC NFT Lifecycle Operations Silently Mutate NFT Ownership Without Emitting NFT-Specific Events - (`File: x/nft-transfer/keeper/packet.go`)

---

### Summary

The `x/nft-transfer` module performs NFT minting, burning, and ownership transfers during IBC packet relay without emitting the NFT-specific events (`mint_nft`, `burn_nft`, `transfer_nft`) that the `x/nft` module defines. Off-chain indexers and clients that reconstruct NFT ownership history by subscribing to those events will silently diverge from on-chain state after any IBC NFT transfer.

---

### Finding Description

The `x/nft` module defines three ownership-change events and emits them exclusively from the message-server layer:

- `mint_nft` — emitted in `msgServer.MintNFT` after calling `m.Keeper.MintNFT`
- `burn_nft` — emitted in `msgServer.BurnNFT` after calling `m.Keeper.BurnNFT`
- `transfer_nft` — emitted in `msgServer.TransferNFT` after calling `m.TransferOwner` [1](#0-0) [2](#0-1) [3](#0-2) 

The keeper-layer functions themselves (`MintNFT`, `BurnNFTUnverified`, `TransferOwner`) emit **no events**: [4](#0-3) [5](#0-4) [6](#0-5) 

The IBC relay path in `x/nft-transfer/keeper/packet.go` calls these keeper functions directly, bypassing the message server entirely, and emits **no NFT-specific events** for any of the three operations:

**`createOutgoingPacket`** — burns vouchers via `BurnNFTUnverified` (sink-chain send) or escrows via `TransferOwner` (source-chain send), with no `burn_nft` or `transfer_nft` event: [7](#0-6) 

**`processReceivedPacket`** — mints vouchers via `MintNFT` (away-from-origin receive) or unescrows via `TransferOwner` (back-to-origin receive), with no `mint_nft` or `transfer_nft` event: [8](#0-7) 

**`refundPacketToken`** — called on acknowledgement failure or timeout, either unescrows via `TransferOwner` or re-mints via `MintNFT`, again with no NFT-specific event: [9](#0-8) 

The only events emitted by the IBC module are the generic `non_fungible_token_packet`, `class_trace`, and `timeout` IBC events, which carry no per-token ownership attribution: [10](#0-9) [11](#0-10) 

---

### Impact Explanation

Any off-chain service (indexer, wallet, explorer, marketplace) that tracks NFT ownership by subscribing to `mint_nft`, `burn_nft`, or `transfer_nft` events will produce an incorrect ownership record for every NFT that passes through an IBC channel. Specifically:

- A user sending an NFT cross-chain (source chain) will appear to still own it on-chain to the indexer, because no `transfer_nft` or `burn_nft` event was emitted.
- A user receiving an NFT cross-chain (destination chain) will not appear as the owner to the indexer, because no `mint_nft` event was emitted.
- On timeout/refund, the re-minted or unescrow'd NFT will not appear in the indexer's ownership record.

The corrupted value is the **per-address NFT ownership set** as reconstructed from event logs. On-chain state remains correct; the divergence is between on-chain state and any event-log-derived view.

---

### Likelihood Explanation

Every IBC NFT transfer — a supported, documented, production entrypoint reachable by any unprivileged user via `MsgTransfer` — triggers this path. The entry point is `msgServer.Transfer` → `SendTransfer` → `createOutgoingPacket`, and the receive side is `OnRecvPacket` → `processReceivedPacket`. No special privilege is required. The likelihood is **high** for any deployment that has active IBC NFT channels and any off-chain consumer of NFT events. [12](#0-11) 

---

### Recommendation

Emit the appropriate NFT-specific events inside `createOutgoingPacket`, `processReceivedPacket`, and `refundPacketToken` after each successful keeper call, mirroring the event attributes defined in `x/nft/types/events.go`: [13](#0-12) 

Alternatively, move event emission into the keeper-layer functions (`MintNFT`, `BurnNFTUnverified`, `TransferOwner`) so that all callers — both message server and IBC relay — automatically emit the correct events.

---

### Proof of Concept

1. User A on Chain-1 owns NFT `(classA, token1)`.
2. User A submits `MsgTransfer` to send `token1` to User B on Chain-2.
3. `createOutgoingPacket` calls `k.nftKeeper.BurnNFTUnverified(ctx, classA, token1, senderAddr)` (sink-chain path) — **no `burn_nft` event emitted**.
4. Chain-2 relayer delivers the packet; `processReceivedPacket` calls `k.nftKeeper.MintNFT(ctx, ibcClassID, token1, ..., escrowAddr, receiverAddr)` — **no `mint_nft` event emitted**.
5. An indexer on Chain-2 scanning `mint_nft` events sees nothing; User B does not appear as owner.
6. An indexer on Chain-1 scanning `burn_nft` events sees nothing; User A still appears as owner.
7. Both indexers now hold ownership state that contradicts on-chain truth, with no mechanism to detect the divergence short of a full state query. [14](#0-13) [15](#0-14)

### Citations

**File:** x/nft/keeper/msg_server.go (L76-89)
```go
	ctx.EventManager().EmitEvents(sdk.Events{
		sdk.NewEvent(
			types.EventTypeMintNFT,
			sdk.NewAttribute(types.AttributeKeyTokenID, msg.Id),
			sdk.NewAttribute(types.AttributeKeyDenomID, msg.DenomId),
			sdk.NewAttribute(types.AttributeKeyTokenURI, msg.URI),
			sdk.NewAttribute(types.AttributeKeyRecipient, msg.Recipient),
		),
		sdk.NewEvent(
			sdk.EventTypeMessage,
			sdk.NewAttribute(sdk.AttributeKeyModule, types.AttributeValueCategory),
			sdk.NewAttribute(sdk.AttributeKeySender, msg.Sender),
		),
	})
```

**File:** x/nft/keeper/msg_server.go (L148-161)
```go
	ctx.EventManager().EmitEvents(sdk.Events{
		sdk.NewEvent(
			types.EventTypeTransfer,
			sdk.NewAttribute(types.AttributeKeyTokenID, msg.Id),
			sdk.NewAttribute(types.AttributeKeyDenomID, msg.DenomId),
			sdk.NewAttribute(types.AttributeKeySender, msg.Sender),
			sdk.NewAttribute(types.AttributeKeyRecipient, msg.Recipient),
		),
		sdk.NewEvent(
			sdk.EventTypeMessage,
			sdk.NewAttribute(sdk.AttributeKeyModule, types.AttributeValueCategory),
			sdk.NewAttribute(sdk.AttributeKeySender, msg.Sender),
		),
	})
```

**File:** x/nft/keeper/msg_server.go (L177-189)
```go
	ctx.EventManager().EmitEvents(sdk.Events{
		sdk.NewEvent(
			types.EventTypeBurnNFT,
			sdk.NewAttribute(types.AttributeKeyDenomID, msg.DenomId),
			sdk.NewAttribute(types.AttributeKeyTokenID, msg.Id),
			sdk.NewAttribute(types.AttributeKeyOwner, msg.Sender),
		),
		sdk.NewEvent(
			sdk.EventTypeMessage,
			sdk.NewAttribute(sdk.AttributeKeyModule, types.AttributeValueCategory),
			sdk.NewAttribute(sdk.AttributeKeySender, msg.Sender),
		),
	})
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

**File:** x/nft-transfer/keeper/packet.go (L35-49)
```go
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
```

**File:** x/nft-transfer/keeper/packet.go (L106-117)
```go
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
```

**File:** x/nft-transfer/keeper/packet.go (L172-179)
```go
		sdkCtx := sdk.UnwrapSDKContext(ctx)
		sdkCtx.EventManager().EmitEvent(
			sdk.NewEvent(
				types.EventTypeClassTrace,
				sdk.NewAttribute(types.AttributeKeyTraceHash, classTrace.Hash().String()),
				sdk.NewAttribute(types.AttributeKeyClassID, voucherClassID),
			),
		)
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

**File:** x/nft-transfer/ibc_module.go (L173-184)
```go
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
```

**File:** x/nft-transfer/keeper/msg_server.go (L15-48)
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

	k.Logger(ctx).Info("IBC non-fungible token transfer",
		"classID", msg.ClassId,
		"tokenIDs", strings.Join(msg.TokenIds, ","),
		"sender", msg.Sender,
		"receiver", msg.Receiver,
	)

	ctx.EventManager().EmitEvents(sdk.Events{
		sdk.NewEvent(
			types.EventTypeTransfer,
			sdk.NewAttribute(sdk.AttributeKeySender, msg.Sender),
			sdk.NewAttribute(types.AttributeKeyReceiver, msg.Receiver),
		),
		sdk.NewEvent(
			sdk.EventTypeMessage,
			sdk.NewAttribute(sdk.AttributeKeyModule, types.ModuleName),
		),
	})

	return &types.MsgTransferResponse{}, nil
```

**File:** x/nft/types/events.go (L1-22)
```go
// Cronos.com Chain Copyright 2018-present Cronos.com
package types

// NFT module event types
var (
	EventTypeIssueDenom = "issue_denom"
	EventTypeTransfer   = "transfer_nft"
	EventTypeEditNFT    = "edit_nft"
	EventTypeMintNFT    = "mint_nft"
	EventTypeBurnNFT    = "burn_nft"

	AttributeValueCategory = ModuleName

	AttributeKeySender    = "sender"
	AttributeKeyCreator   = "creator"
	AttributeKeyRecipient = "recipient"
	AttributeKeyOwner     = "owner"
	AttributeKeyTokenID   = "token_id"
	AttributeKeyTokenURI  = "token_uri"
	AttributeKeyDenomID   = "denom_id"
	AttributeKeyDenomName = "denom_name"
)
```
