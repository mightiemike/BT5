### Title
Missing Blocked-Address Check Before IBC NFT Minting Permanently Locks NFTs - (File: `x/nft-transfer/keeper/packet.go`)

### Summary
`processReceivedPacket` mints IBC NFT vouchers directly to the caller-supplied `receiver` address without verifying whether that address is a blocked module account. Because module accounts cannot sign transactions, any NFT minted to one is permanently irrecoverable, and the corresponding escrowed NFT on the source chain is also permanently locked.

### Finding Description
In `processReceivedPacket`, the receiver address is decoded from the packet data and passed directly to `MintNFT` with no check against the chain's blocked-address set:

```go
receiver, err := sdk.AccAddressFromBech32(data.Receiver)
// ... no blocked-address check ...
if err := k.nftKeeper.MintNFT(ctx, voucherClassID, tokenID, "", data.TokenUris[i], "", escrowAddress, receiver); err != nil {
    return err
}
``` [1](#0-0) 

`MintNFTUnverified`, which is the underlying write path, performs no recipient capability check either — it only validates that the denom exists and the token ID is not a duplicate: [2](#0-1) 

The nft-transfer `Keeper` holds an `authKeeper` field but uses it exclusively in `SetEscrowAddress` to create the escrow account; it is never consulted to guard the receiver in the receive path: [3](#0-2) 

The application defines a `BlockedAddrs()` map of module accounts that must not receive external assets, but this map is wired only into the bank keeper — the nft-transfer keeper has no reference to it: [4](#0-3) 

The ante-handler `ValidateMsgTransferDecorator` only enforces field-length limits on `MsgTransfer`; it does not reject module-account receiver addresses: [5](#0-4) 

`ValidateBasic` on `NonFungibleTokenPacketData` accepts any valid bech32 string as the receiver, which module account addresses satisfy: [6](#0-5) 

### Impact Explanation
When an IBC NFT packet is successfully received and `processReceivedPacket` returns `nil`, the IBC core layer writes a success acknowledgement. The source chain's `OnAcknowledgementPacket` then takes the success branch and does nothing — the escrowed NFT on the source chain remains permanently locked in the escrow address. Simultaneously, the minted voucher NFT on the destination chain is owned by a module account that can never sign a `MsgTransferNFT`, `MsgBurnNFT`, or `MsgTransfer` transaction. Both the original NFT (escrowed on the source chain) and the IBC voucher (owned by the module account on the destination chain) are irretrievably lost. The corrupted state is the NFT ownership record in the nft module's KV store and the escrowed token record in the nft-transfer escrow address. [7](#0-6) 

### Likelihood Explanation
Likelihood is low. The sender must supply a module account address (e.g., `cosmos1jv65s3grqf6v6jl3dp4t6c9t9rk99cd88lyufl` for the distribution module) as the `Receiver` field of `MsgTransfer`. This can occur through a copy-paste error, a misconfigured integration, or a cross-chain application that resolves addresses programmatically. Module account addresses are valid bech32 strings indistinguishable from user addresses at the protocol level, making accidental submission plausible.

### Recommendation
Before minting or transferring an NFT to the receiver in `processReceivedPacket`, add a blocked-address guard. The standard ICS-20 fungible transfer module performs an equivalent check via `bankKeeper.BlockedAddr(receiver)`. The nft-transfer keeper should either accept a `BankKeeper` dependency and call `BlockedAddr`, or expose the blocked-address map through the existing `authKeeper` interface. If the receiver is blocked, `processReceivedPacket` should return an error so that the IBC core layer writes a failure acknowledgement and `refundPacketToken` is triggered on the source chain, returning the NFT to the sender. [8](#0-7) 

### Proof of Concept
1. On chain A, Alice owns NFT `(denomClass, token1)`.
2. Alice submits `MsgTransfer` with `Receiver` set to the bech32 address of chain B's `distribution` module account (a blocked address).
3. The relayer picks up the packet and submits it to chain B.
4. `OnRecvPacket` → `processReceivedPacket` is called. `sdk.AccAddressFromBech32` succeeds (the address is valid bech32). No blocked-address check is performed.
5. `MintNFT` succeeds; the IBC voucher NFT is recorded as owned by the `distribution` module account in chain B's nft KV store.
6. `processReceivedPacket` returns `nil`; IBC core writes a success acknowledgement.
7. Chain A's `OnAcknowledgementPacket` takes the success branch — no refund is issued. `token1` remains permanently escrowed at chain A's nft-transfer escrow address.
8. On chain B, the `distribution` module account cannot sign `MsgTransferNFT` or any other transaction. The voucher NFT is permanently locked.

Both `token1` (escrowed on chain A) and its IBC voucher (owned by the module account on chain B) are irrecoverable.

### Citations

**File:** x/nft-transfer/keeper/packet.go (L140-185)
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
```

**File:** x/nft/keeper/keeper.go (L44-68)
```go
// MintNFTUnverified mints an NFT without verifying if the owner is the creator of denom
// Needed during genesis initialization
func (k Keeper) MintNFTUnverified(ctx sdk.Context, denomID, tokenID, tokenNm, tokenURI, tokenData string, owner sdk.AccAddress) error {
	if !k.HasDenomID(ctx, denomID) {
		return sdkerrors.Wrapf(types.ErrInvalidDenom, "denom ID %s not exists", denomID)
	}

	if k.HasNFT(ctx, denomID, tokenID) {
		return sdkerrors.Wrapf(types.ErrNFTAlreadyExists, "NFT %s already exists in collection %s", tokenID, denomID)
	}

	k.setNFT(
		ctx, denomID,
		types.NewBaseNFT(
			tokenID,
			tokenNm,
			owner,
			tokenURI,
			tokenData,
		),
	)
	k.setOwner(ctx, denomID, tokenID, owner)
	k.increaseSupply(ctx, denomID)

	return nil
```

**File:** x/nft-transfer/keeper/keeper.go (L15-42)
```go
type Keeper struct {
	storeKey storetypes.StoreKey
	cdc      codec.BinaryCodec

	ics4Wrapper   types.ICS4Wrapper
	channelKeeper types.ChannelKeeper
	nftKeeper     types.NFTKeeper
	authKeeper    types.AccountKeeper
}

// NewKeeper creates a new IBC nft-transfer Keeper instance
func NewKeeper(
	cdc codec.BinaryCodec,
	key storetypes.StoreKey,
	ics4Wrapper types.ICS4Wrapper,
	channelKeeper types.ChannelKeeper,
	nftKeeper types.NFTKeeper,
	authKeeper types.AccountKeeper,
) Keeper {
	return Keeper{
		cdc:           cdc,
		storeKey:      key,
		ics4Wrapper:   ics4Wrapper,
		channelKeeper: channelKeeper,
		nftKeeper:     nftKeeper,
		authKeeper:    authKeeper,
	}
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

**File:** app/ante.go (L77-109)
```go
func (vtd ValidateMsgTransferDecorator) AnteHandle(ctx sdk.Context, tx sdk.Tx, simulate bool, next sdk.AnteHandler) (sdk.Context, error) {
	// avoid breaking consensus
	if !ctx.IsCheckTx() {
		return next(ctx, tx, simulate)
	}

	msgs := tx.GetMsgs()
	for _, msg := range msgs {
		transfer, ok := msg.(*nfttypes.MsgTransfer)
		if !ok {
			continue
		}

		if len(transfer.ClassId) > MaxClassIDLength {
			return ctx, newsdkerrors.Wrapf(sdkerrors.ErrInvalidRequest, "class id length must be less than %d", MaxClassIDLength)
		}

		if len(transfer.TokenIds) > MaxTokenIds {
			return ctx, newsdkerrors.Wrapf(sdkerrors.ErrInvalidRequest, "token id length must be less than %d", MaxTokenIds)
		}

		for _, tokenID := range transfer.TokenIds {
			if len(tokenID) > MaxTokenIDLength {
				return ctx, newsdkerrors.Wrapf(sdkerrors.ErrInvalidRequest, "token id length must be less than %d", MaxTokenIDLength)
			}
		}

		if len(transfer.Receiver) > MaximumReceiverLength {
			return ctx, newsdkerrors.Wrapf(sdkerrors.ErrInvalidRequest, "receiver length must be less than %d", MaximumReceiverLength)
		}
	}

	return next(ctx, tx, simulate)
```

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

**File:** x/nft-transfer/keeper/relay.go (L117-126)
```go
func (k Keeper) OnAcknowledgementPacket(ctx sdk.Context, channelVersion string, packet channeltypes.Packet, data types.NonFungibleTokenPacketData, ack channeltypes.Acknowledgement) error {
	switch ack.Response.(type) {
	case *channeltypes.Acknowledgement_Error:
		return k.refundPacketToken(ctx, packet, data)
	default:
		// the acknowledgement succeeded on the receiving chain so nothing
		// needs to be executed and no error needs to be returned
		return nil
	}
}
```
