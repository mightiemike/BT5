### Title
Missing Escrow Address Guard in `processReceivedPacket` Allows Permanent NFT Lock via Malicious IBC Packet — (`x/nft-transfer/keeper/packet.go`)

---

### Summary

A malicious IBC counterparty chain can craft a packet whose `data.Receiver` field is set to the bech32 encoding of the receiving chain's own escrow address (`GetEscrowAddress("nft", "channel-X")`). Because neither `ValidateBasic` nor `processReceivedPacket` checks that the receiver is not the escrow address, the module mints the voucher NFT directly to the escrow address. Since the escrow address has no private key, the NFT is permanently locked with no recovery path.

---

### Finding Description

**Step 1 — `ValidateBasic` passes unconditionally for the escrow address.**

`ValidateBasic` only checks that `Receiver` is non-blank and is a valid bech32 address: [1](#0-0) 

The escrow address produced by `GetEscrowAddress` is a standard 20-byte address encoded as bech32 — it passes this check without issue. There is no denylist or escrow-address comparison here.

**Step 2 — `processReceivedPacket` mints to `receiver` without checking `receiver != escrowAddress`.**

In the `isAwayFromOrigin` branch (the normal "new voucher" path), the code computes both `receiver` and `escrowAddress` independently, then calls `MintNFT` with `receiver` as the owner: [2](#0-1) [3](#0-2) 

If `data.Receiver` was set to `bech32(GetEscrowAddress(destPort, destChannel))`, then `receiver == escrowAddress` at the point of the `MintNFT` call. The NFT is minted with the escrow address as its owner.

**Step 3 — The escrow address is a keyless regular account.**

`SetEscrowAddress` creates the escrow account via `NewAccountWithAddress` — a plain base account, not a module account: [4](#0-3) 

The address is a deterministic SHA-256 hash of `"ics721-1\x00nft/channel-X"`: [5](#0-4) 

No private key exists for this address. Any NFT minted to it is irrecoverable.

**Step 4 — `MintNFT` interface confirms `owner` is the last parameter.** [6](#0-5) 

The `owner` argument receives `receiver`, which equals `escrowAddress` in the attack scenario.

---

### Impact Explanation

The voucher NFT is minted to the escrow address and permanently locked. No user can claim it because the escrow address has no signing key. The NFT's economic value is destroyed. This matches the Critical scope: permanent lock of economically valuable NFTs via an unprivileged on-chain action (IBC packet relay).

---

### Likelihood Explanation

The attack requires a malicious or compromised IBC counterparty chain — a standard threat model in IBC security. The escrow address is deterministic and publicly computable from the port/channel identifiers. No special privileges beyond operating a counterparty chain are needed. The packet passes all existing validation checks without modification.

---

### Recommendation

Add an explicit guard in `processReceivedPacket` immediately after decoding `receiver`, before any NFT operation:

```go
receiver, err := sdk.AccAddressFromBech32(data.Receiver)
if err != nil {
    return err
}

// Guard: receiver must not be the escrow address for this channel
escrowAddress := types.GetEscrowAddress(packet.GetDestPort(), packet.GetDestChannel())
if receiver.Equals(escrowAddress) {
    return sdkerrors.Wrapf(sdkerrors.ErrInvalidAddress,
        "receiver cannot be the escrow address %s", escrowAddress)
}
```

Optionally, also add the same check to `ValidateBasic` if the escrow address is known at validation time, or document that this check must live in the keeper.

---

### Proof of Concept

```go
func TestProcessReceivedPacket_ReceiverIsEscrow(t *testing.T) {
    // Compute the escrow address for destPort="nft", destChannel="channel-0"
    escrowAddr := types.GetEscrowAddress("nft", "channel-0")

    // Craft packet data with Receiver = bech32(escrowAddr)
    data := types.NonFungibleTokenPacketData{
        ClassId:   "originClass",          // not prefixed → isAwayFromOrigin = true
        TokenIds:  []string{"token1"},
        TokenUris: []string{"uri1"},
        Sender:    validSenderBech32,
        Receiver:  escrowAddr.String(),    // <-- attacker sets this
    }

    packet := channeltypes.Packet{
        SourcePort:      "nft",
        SourceChannel:   "channel-1",
        DestinationPort: "nft",
        DestinationChannel: "channel-0",
        // ...
    }

    err := keeper.OnRecvPacket(ctx, types.Version, packet, data)
    require.NoError(t, err) // passes — no guard exists

    // Assert: NFT owner should NOT be the escrow address
    nft, _ := nftKeeper.GetNFT(ctx, "ibc/<hash>", "token1")
    require.NotEqual(t, escrowAddr.String(), nft.GetOwner().String(),
        "NFT was minted to escrow address and is permanently locked")
}
```

The test will fail on the final assertion, confirming the NFT is minted to the escrow address and permanently locked.

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

**File:** x/nft-transfer/keeper/packet.go (L143-151)
```go
	receiver, err := sdk.AccAddressFromBech32(data.Receiver)
	if err != nil {
		return err
	}

	isAwayFromOrigin := types.IsAwayFromOrigin(packet.GetSourcePort(), packet.GetSourceChannel(), data.ClassId)

	// create the escrow address for creating denom and minting nft
	escrowAddress := types.GetEscrowAddress(packet.GetDestPort(), packet.GetDestChannel())
```

**File:** x/nft-transfer/keeper/packet.go (L181-184)
```go
		for i, tokenID := range data.TokenIds {
			if err := k.nftKeeper.MintNFT(ctx, voucherClassID, tokenID, "", data.TokenUris[i], "", escrowAddress, receiver); err != nil {
				return err
			}
```

**File:** x/nft-transfer/keeper/keeper.go (L62-68)
```go
func (k Keeper) SetEscrowAddress(ctx sdk.Context, portID, channelID string) {
	// create the escrow address for the tokens
	escrowAddress := types.GetEscrowAddress(portID, channelID)
	if !k.authKeeper.HasAccount(ctx, escrowAddress) {
		acc := k.authKeeper.NewAccountWithAddress(ctx, escrowAddress)
		k.authKeeper.SetAccount(ctx, acc)
	}
```

**File:** x/nft-transfer/types/keys.go (L45-55)
```go
func GetEscrowAddress(portID, channelID string) sdk.AccAddress {
	// a slash is used to create domain separation between port and channel identifiers to
	// prevent address collisions between escrow addresses created for different channels
	contents := fmt.Sprintf("%s/%s", portID, channelID)

	// ADR 028 AddressHash construction
	preImage := []byte(Version)
	preImage = append(preImage, 0)
	preImage = append(preImage, contents...)
	hash := sha256.Sum256(preImage)
	return hash[:20]
```

**File:** x/nft-transfer/types/expected_keepers.go (L39-43)
```go
	MintNFT(
		ctx sdk.Context, denomID, tokenID, tokenNm,
		tokenURI, tokenData string, sender, owner sdk.AccAddress,
	) error
	BurnNFTUnverified(ctx sdk.Context, denomID, tokenID string, owner sdk.AccAddress) error
```
