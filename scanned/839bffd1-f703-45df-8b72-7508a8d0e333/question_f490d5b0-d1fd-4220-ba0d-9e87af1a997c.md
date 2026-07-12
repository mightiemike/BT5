[File: 'File Name: cmd/chain-maind/opendb/opendb_rocksdb.go -> Scope: Critical. IBC NFT transfer escrow, burn, mint, class-trace, acknowledgement, timeout, or refund flaw enables duplicate withdrawal, unauthorized voucher minting, unauthorized unescrow, or loss of NFTs.'] [Function: NonFungibleTokenPacketData.ValidateBasic / processReceivedPacket / MintNFT] Can an attacker controlling the counterparty chain submit a packet with duplicate entries in data.TokenIds (e.g., ['t1','t1']) and matching data.TokenUris under the precondition that NonFungibleTokenPacketData.ValidateBasic only checks len(TokenIds)==len(TokenUris) but not intra-slice uniqueness, triggering OnRecvPacket -> processReceivedPacket -> MintNFT(voucherClassID,'t1',...) twice in sequence, violating the invariant that each (classID, tokenID) pair is minted at most once per packet, causing scoped

### Citations

**File:** x/nft-transfer/keeper/packet.go (L21-52)
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
			if err := k.nftKeeper.MintNFT(ctx, voucherClassID, tokenID,
