### Title
Missing Timeout Validation in `MsgTransfer.ValidateBasic` Allows Permanent NFT Escrow Lock — (File: `x/nft-transfer/types/msgs.go`)

---

### Summary

`MsgTransfer.ValidateBasic()` in the `x/nft-transfer` module does not enforce that at least one of `TimeoutHeight` or `TimeoutTimestamp` is non-zero. A user submitting a transfer via gRPC or REST with both fields set to `0` disables all timeout protection, causing the NFT to be permanently escrowed with no on-chain mechanism to reclaim it. This is the direct Cosmos analog of setting `deadline = block.timestamp`: both produce an operation with no effective time bound.

---

### Finding Description

`MsgTransfer.ValidateBasic()` validates port, channel, classID, tokenIDs, sender, and receiver, but contains no check that at least one timeout field is non-zero: [1](#0-0) 

The inline comment at line 51 explicitly documents this permissive design: `"timeout height or timestamp values can be 0 to disable the timeout."` Setting both to `0` disables both simultaneously.

The CLI path in `tx.go` does enforce a non-zero relative timestamp when `absoluteTimeouts = false`: [2](#0-1) 

However, this guard exists only in the CLI command handler. Any user submitting `MsgTransfer` directly via gRPC or the REST gateway bypasses `NewTransferTxCmd` entirely and reaches `ValidateBasic` directly, where no equivalent check exists.

The default timeout constant confirms that `0` is the sentinel for "disabled": [3](#0-2) 

When `SendTransfer` is called with both timeouts at `0`, `createOutgoingPacket` escrows the NFT (or burns the voucher) before the packet is sent: [4](#0-3) 

The packet is then submitted to the IBC core layer with no expiry. Because `OnTimeoutPacket` — the only refund path — is never triggered for a packet with no timeout, the escrowed NFT is permanently locked in the escrow address. [5](#0-4) 

---

### Impact Explanation

The NFT is transferred to the escrow address (`types.GetEscrowAddress(sourcePort, sourceChannel)`) during `createOutgoingPacket`. With no timeout set, `OnTimeoutPacket` is never invoked, so `refundPacketToken` — the only code path that returns the NFT to the sender — is unreachable. The NFT owner loses permanent custody of their asset. The corrupted invariant is NFT ownership: the original owner's address is replaced by the escrow module address with no recovery path.

---

### Likelihood Explanation

The attack surface is any user who constructs a `MsgTransfer` programmatically (SDK client, custom relayer tooling, REST API) and omits or zeroes both timeout fields. This is a realistic mistake for developers integrating against the module directly. No privileged role, leaked key, or social engineering is required. The entry path is a standard, unprivileged Cosmos SDK transaction.

---

### Recommendation

Add a check in `MsgTransfer.ValidateBasic()` that rejects messages where both `TimeoutHeight` and `TimeoutTimestamp` are zero:

```go
if msg.TimeoutHeight.IsZero() && msg.TimeoutTimestamp == 0 {
    return newsdkerrors.Wrap(ErrInvalidTimeout,
        "both timeout height and timeout timestamp cannot be zero")
}
```

This mirrors the protection already present in the CLI handler and closes the gap for all non-CLI submission paths.

---

### Proof of Concept

1. Construct a `MsgTransfer` with `TimeoutHeight = {0, 0}` and `TimeoutTimestamp = 0` targeting a valid IBC channel.
2. Submit via gRPC (`/cosmos.tx.v1beta1.Service/BroadcastTx`) or REST (`POST /cosmos/tx/v1beta1/txs`).
3. `ValidateBasic` passes — no timeout check exists.
4. `createOutgoingPacket` escrows the NFT to the escrow address.
5. The IBC packet is committed on-chain with no expiry.
6. Because the packet never times out, `OnTimeoutPacket` is never called, `refundPacketToken` is never executed, and the NFT remains permanently locked in the escrow address with no recovery path for the original owner.

### Citations

**File:** x/nft-transfer/types/msgs.go (L50-88)
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
}
```

**File:** x/nft-transfer/client/cli/tx.go (L84-86)
```go
				if timeoutTimestamp == 0 {
					return errors.New("relative timeouts must provide a non zero value timestamp")
				}
```

**File:** x/nft-transfer/types/packet.go (L13-17)
```go
// DefaultRelativePacketTimeoutTimestamp is the default packet timeout timestamp (in nanoseconds)
// relative to the current block timestamp of the counterparty chain provided by the client
// state. The timeout is disabled when set to 0. The default is currently set to a 10 minute
// timeout.
var DefaultRelativePacketTimeoutTimestamp = uint64((time.Duration(10) * time.Minute).Nanoseconds())
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

**File:** x/nft-transfer/keeper/relay.go (L128-132)
```go
// OnTimeoutPacket refunds the sender since the original packet sent was
// never received and has been timed out.
func (k Keeper) OnTimeoutPacket(ctx sdk.Context, channelVersion string, packet channeltypes.Packet, data types.NonFungibleTokenPacketData) error {
	return k.refundPacketToken(ctx, packet, data)
}
```
