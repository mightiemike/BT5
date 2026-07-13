### Title
ICS-721 Non-Compliance: `NonFungibleTokenPacketData.ValidateBasic` Rejects Valid Packets from Non-Bech32 Chains - (File: `x/nft-transfer/types/packet.go`)

### Summary

`NonFungibleTokenPacketData.ValidateBasic()` enforces Bech32 address validation on both `Sender` and `Receiver` fields, directly contradicting the ICS-721 specification and the function's own comment. This causes `OnRecvPacket` to write an error acknowledgement for any IBC NFT packet originating from a non-Bech32 chain (e.g., an EVM chain), permanently blocking cross-chain NFT receipt from such chains.

### Finding Description

The `ValidateBasic()` function in `x/nft-transfer/types/packet.go` carries an explicit comment stating that address formats must **not** be validated:

```go
// NOTE: The addresses formats are not validated as the sender and recipient can have different
// formats defined by their corresponding chains that are not known to IBC.
func (nftpd NonFungibleTokenPacketData) ValidateBasic() error {
```

Despite this, the function immediately proceeds to call `sdk.AccAddressFromBech32` on both addresses:

```go
if _, err := sdk.AccAddressFromBech32(nftpd.Sender); err != nil {
    return newsdkerrors.Wrap(sdkerrors.ErrInvalidAddress, "invalid sender address")
}
...
if _, err := sdk.AccAddressFromBech32(nftpd.Receiver); err != nil {
    return newsdkerrors.Wrap(sdkerrors.ErrInvalidAddress, "invalid receiver address")
}
```

This `ValidateBasic()` is called unconditionally at the top of `OnRecvPacket` in `x/nft-transfer/keeper/relay.go`:

```go
func (k Keeper) OnRecvPacket(...) error {
    if err := data.ValidateBasic(); err != nil {
        return err
    }
    return k.processReceivedPacket(ctx, packet, data)
}
```

By contrast, `MsgTransfer.ValidateBasic()` in `x/nft-transfer/types/msgs.go` correctly omits receiver validation with the note: *"The recipient addresses format is not validated as the format defined by the chain is not known to IBC."*

The ICS-721 spec explicitly states that address formats are chain-specific and must not be validated by the receiving module. The `Sender` field in a received packet is the address on the **source** chain, which may use any format (hex, bech32, etc.).

### Impact Explanation

Any IBC NFT packet sent from a non-Bech32 chain (e.g., an EVM chain using `0x...` hex addresses) to Cronos POS Chain will be rejected at `ValidateBasic()` because `sdk.AccAddressFromBech32(nftpd.Sender)` fails for non-Bech32 sender strings. The `OnRecvPacket` handler returns an error, causing `IBCModule.OnRecvPacket` to write an error acknowledgement. The sending chain then refunds the sender via `OnAcknowledgementPacket` → `refundPacketToken`. The NFT never arrives on Cronos POS Chain.

The corrupted invariant: **IBC NFT transfers from non-Bech32 chains to Cronos POS Chain are permanently and silently blocked**, even though the packet is fully valid per the ICS-721 specification.

### Likelihood Explanation

The IBC ecosystem includes EVM-compatible chains (e.g., Evmos, Injective EVM, Ethereum via IBC bridges) that use non-Bech32 address formats. Any operator establishing an ICS-721 channel between such a chain and Cronos POS Chain will find that all inbound NFT transfers fail. This is triggered by a normal, unprivileged `MsgTransfer` on the counterparty chain followed by a relayer submitting the packet — no special privileges required.

### Recommendation

Remove the `sdk.AccAddressFromBech32` calls for both `Sender` and `Receiver` in `NonFungibleTokenPacketData.ValidateBasic()`, consistent with the function's own comment and the ICS-721 specification. Retain only the blank-string checks. The receiver address is parsed from Bech32 later in `processReceivedPacket` only when the receiving chain (Cronos POS) needs to credit the NFT — that parse is appropriate since the receiver must be a valid local address.

### Proof of Concept

1. Chain A (EVM, sender address `0xdeadbeef...`) initiates `MsgTransfer` of an NFT to Cronos POS Chain.
2. The packet's `Sender` field is set to `0xdeadbeef...` (a valid EVM address, not Bech32).
3. A relayer submits the packet to Cronos POS Chain; `IBCModule.OnRecvPacket` is called.
4. `k.OnRecvPacket` calls `data.ValidateBasic()`.
5. `sdk.AccAddressFromBech32("0xdeadbeef...")` returns an error.
6. `ValidateBasic()` returns `"invalid sender address"`.
7. `OnRecvPacket` returns an error; an error acknowledgement is written.
8. Chain A's `OnAcknowledgementPacket` calls `refundPacketToken`, returning the NFT to the sender.
9. The NFT never arrives on Cronos POS Chain despite the transfer being fully ICS-721 compliant.

**Root cause lines:** [1](#0-0) 

**Call site in `OnRecvPacket`:** [2](#0-1) 

**Contrast with `MsgTransfer.ValidateBasic` (correct behavior):** [3](#0-2)

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

**File:** x/nft-transfer/types/msgs.go (L50-87)
```go
// ValidateBasic performs a basic check of the MsgTransfer fields.
// NOTE: timeout height or timestamp values can be 0 to disable the timeout.
// NOTE: The recipient addresses format is not validated as the format defined by
// the chain is not known to IBC.
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
```
