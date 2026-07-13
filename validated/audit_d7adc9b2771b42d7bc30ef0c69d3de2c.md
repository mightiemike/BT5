### Title
Wrong-Channel Voucher Escrow Instead of Burn Creates Spurious NFT Copy — (`x/nft-transfer/keeper/packet.go`)

### Summary

`createOutgoingPacket` computes `isAwayFromOrigin` by comparing the outgoing `sourcePort/sourceChannel` against the leading segment of the NFT's `fullClassPath`. When a voucher NFT is sent back toward its origin via **any channel other than the one encoded in its trace path**, `isAwayFromOrigin` evaluates to `true`, causing the voucher to be **escrowed** instead of **burned**. The receiving chain simultaneously **mints** a new voucher (because it also sees `isAwayFromOrigin=true`). The result is two live representations of the same NFT: the original locked on the origin chain, the original voucher locked in escrow on the sink chain, and a new spurious voucher minted on the destination chain.

---

### Finding Description

**`IsAwayFromOrigin` logic** — `x/nft-transfer/types/trace.go` lines 49-52:

```go
func IsAwayFromOrigin(sourcePort, sourceChannel, fullClassPath string) bool {
    prefixClassID := GetClassPrefix(sourcePort, sourceChannel)
    return !strings.HasPrefix(fullClassPath, prefixClassID)
}
``` [1](#0-0) 

The function returns `false` (i.e., "going back to origin / burn it") **only** when `fullClassPath` starts with `sourcePort/sourceChannel/`. If the attacker uses any other valid channel, the prefix check fails and the function returns `true`.

**`createOutgoingPacket` branch** — `x/nft-transfer/keeper/packet.go` lines 91-117:

```go
isAwayFromOrigin := types.IsAwayFromOrigin(sourcePort, sourceChannel, fullClassPath)
...
if isAwayFromOrigin {
    escrowAddress := types.GetEscrowAddress(sourcePort, sourceChannel)
    k.nftKeeper.TransferOwner(ctx, classID, tokenID, sender, escrowAddress)  // ESCROW
} else {
    k.nftKeeper.BurnNFTUnverified(ctx, classID, tokenID, sender)             // BURN
}
``` [2](#0-1) 

There is **no validation** that the outgoing channel matches the channel encoded in the NFT's trace path. `ValidateBasic` in `msgs.go` only checks that the source port equals `PortID` and that the channel identifier is syntactically valid — it never cross-checks the channel against the NFT's class trace. [3](#0-2) 

**`processReceivedPacket` on the destination chain** — `x/nft-transfer/keeper/packet.go` lines 148-185:

The receiving chain runs the same `IsAwayFromOrigin` check with the packet's source port/channel and the `data.ClassId`. Because the wrong channel was used, it also evaluates to `true`, so the destination chain **mints** a new voucher instead of unescrowing the original NFT. [4](#0-3) 

---

### Impact Explanation

**Concrete attack scenario** (two channels between chain B and chain A):

| Step | Chain | State |
|------|-------|-------|
| A→B via `(p1,c1)→(p2,c2)` | A | `nftClass` escrowed |
| | B | voucher `ibc/{sha256("p2/c2/nftClass")}` minted to attacker |
| Attacker sends via wrong channel `(p3,c3)→(p4,c4)` | B | `IsAwayFromOrigin("p3","c3","p2/c2/nftClass")=true` → voucher **escrowed** (not burned) |
| Packet received on A | A | `IsAwayFromOrigin("p3","c3","p2/c2/nftClass")=true` → new voucher `ibc/{sha256("p4/c4/p2/c2/nftClass")}` **minted** to attacker |

After the attack:
- Chain A: original `nftClass` permanently locked in escrow + spurious new voucher minted to attacker
- Chain B: original voucher permanently locked in escrow (packet succeeded, no refund)

The original NFT is **permanently unrecoverable** — the voucher that would have burned to release it is now locked in escrow on B, and the proper burn path can never be triggered. The attacker holds a new, unbacked voucher on A that can be further transferred to inflate supply across additional chains.

---

### Likelihood Explanation

- Requires only a standard `MsgTransfer` transaction — no privileged access, no governance, no operator compromise.
- Multiple IBC channels between the same two chains are common in production deployments.
- The attacker needs only to own a voucher NFT and submit a transfer using a different valid channel.
- No existing guard in `ValidateBasic`, `SendTransfer`, or `createOutgoingPacket` prevents this. [5](#0-4) 

---

### Recommendation

Before escrowing or burning, validate that the outgoing `sourcePort/sourceChannel` matches the leading port/channel segment of the NFT's `fullClassPath` when the NFT is a voucher (i.e., when `isAwayFromOrigin` would be `false` under the correct channel). Concretely, in `createOutgoingPacket`, after resolving `fullClassPath`, check:

```go
// If the classID is an IBC voucher, the outgoing channel must match
// the leading segment of the full class path (the "return" channel).
if strings.HasPrefix(classID, nfttypes.IBCPrefix) {
    expectedPrefix := types.GetClassPrefix(sourcePort, sourceChannel)
    if !strings.HasPrefix(fullClassPath, expectedPrefix) {
        // This is a voucher being sent via the wrong channel — reject.
        return channeltypes.Packet{}, types.ErrInvalidClassID.Wrapf(
            "voucher %s must be returned via channel matching its trace prefix", classID)
    }
}
```

This ensures a voucher can only be sent back through the channel that created it, preserving the burn invariant.

---

### Proof of Concept

```go
func TestWrongChannelEscrowInsteadOfBurn(t *testing.T) {
    // Setup: NFT arrived on sink chain via p2/c2, fullClassPath = "p2/c2/nftClass"
    fullClassPath := "p2/c2/nftClass"

    // Correct channel: p2/c2 → isAwayFromOrigin = false (burn)
    assert.False(t, types.IsAwayFromOrigin("p2", "c2", fullClassPath))

    // Attack: attacker uses different channel p3/c3 → isAwayFromOrigin = true (escrow!)
    assert.True(t, types.IsAwayFromOrigin("p3", "c3", fullClassPath))

    // In createOutgoingPacket with sourcePort=p3, sourceChannel=c3:
    // isAwayFromOrigin=true → TransferOwner to escrow (NOT BurnNFTUnverified)
    // Packet data ClassId = "p2/c2/nftClass" is sent to destination chain.
    //
    // On destination chain (chain A), processReceivedPacket:
    // IsAwayFromOrigin("p3","c3","p2/c2/nftClass") = true → MintNFT (new spurious voucher)
    // Original nftClass on chain A remains permanently escrowed.
}
```

### Citations

**File:** x/nft-transfer/types/trace.go (L49-52)
```go
func IsAwayFromOrigin(sourcePort, sourceChannel, fullClassPath string) bool {
	prefixClassID := GetClassPrefix(sourcePort, sourceChannel)
	return !strings.HasPrefix(fullClassPath, prefixClassID)
}
```

**File:** x/nft-transfer/keeper/packet.go (L91-117)
```go
	isAwayFromOrigin := types.IsAwayFromOrigin(sourcePort,
		sourceChannel, fullClassPath)

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
```

**File:** x/nft-transfer/keeper/packet.go (L148-185)
```go
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
```

**File:** x/nft-transfer/types/msgs.go (L54-88)
```go
func (msg MsgTransfer) ValidateBasic() error {
	if err := host.PortIdentifierValidator(msg.SourcePort); err != nil {
		return newsdkerrors.Wrap(err, "invalid source port ID")
	}
	if msg.SourcePort != PortID {
		return newsdkerrors.Wrapf(ErrInvalidSourcePort, "source port must be %q", PortID)
	}
	if err := host.ChannelIdentifierValidator(msg.SourceChannel); err != nil {
		return newsdkerrors.Wrap(err, "invalid source channel ID")
	}

	if strings.TrimSpace(msg.ClassId) == "" {
		return newsdkerrors.Wrap(ErrInvalidClassID, "classId cannot be blank")
	}

	if len(msg.TokenIds) == 0 {
		return newsdkerrors.Wrap(ErrInvalidTokenID, "tokenId cannot be blank")
	}

	for _, tokenID := range msg.TokenIds {
		if strings.TrimSpace(tokenID) == "" {
			return newsdkerrors.Wrap(ErrInvalidTokenID, "tokenId cannot be blank")
		}
	}

	// NOTE: sender format must be validated as it is required by the GetSigners function.
	_, err := sdk.AccAddressFromBech32(msg.Sender)
	if err != nil {
		return newsdkerrors.Wrapf(sdkerrors.ErrInvalidAddress, "string could not be parsed as address: %v", err)
	}
	if strings.TrimSpace(msg.Receiver) == "" {
		return newsdkerrors.Wrap(sdkerrors.ErrInvalidAddress, "missing recipient address")
	}
	return nil
}
```

**File:** x/nft-transfer/keeper/relay.go (L50-93)
```go
	if sourcePort != types.PortID {
		return sdkerrors.Wrapf(types.ErrInvalidSourcePort, "source port must be %q", types.PortID)
	}

	sourceChannelEnd, found := k.channelKeeper.GetChannel(ctx, sourcePort, sourceChannel)
	if !found {
		return sdkerrors.Wrapf(channeltypes.ErrChannelNotFound, "port ID (%s) channel ID (%s)", sourcePort, sourceChannel)
	}

	destinationPort := sourceChannelEnd.Counterparty.PortId
	destinationChannel := sourceChannelEnd.Counterparty.ChannelId

	// get the next sequence
	sequence, found := k.channelKeeper.GetNextSequenceSend(ctx, sourcePort, sourceChannel)
	if !found {
		return sdkerrors.Wrapf(
			channeltypes.ErrSequenceSendNotFound,
			"source port: %s, source channel: %s", sourcePort, sourceChannel,
		)
	}

	// See spec for this logic: https://github.com/cosmos/ibc/blob/master/spec/app/ics-721-nft-transfer/README.md#packet-relay
	packet, err := k.createOutgoingPacket(ctx,
		sourcePort,
		sourceChannel,
		destinationPort,
		destinationChannel,
		classID,
		tokenIDs,
		sender,
		receiver,
		sequence,
		timeoutHeight,
		timeoutTimestamp,
	)
	if err != nil {
		return err
	}

	if _, err := k.ics4Wrapper.SendPacket(ctx, sourcePort, sourceChannel, timeoutHeight, timeoutTimestamp, packet.GetData()); err != nil {
		return err
	}

	return nil
```
