[File: 'proto/chainmain/supply/v1/genesis.proto -> Scope: Critical. NFT module authorization or ownership invariant break lets an attacker mint, transfer, burn, edit, or seize denominations or NFTs they do not control.'] [Function: refundPacketToken / MintNFT / IsDenomCreator] Can an IBC timeout or error ack on a sink-chain send fail to refund the sender's NFT under the precondition that refundPacketToken (x/nft-transfer/keeper/packet.go) calls MintNFT(sender=escrowAddress, voucherClassID, tokenID) which requires IsDenomCreator(escrowAddress), but the voucher denom was deleted or never existed on the source chain, triggering the call sequence SendTransfer(sink) -> BurnNFTUnverified -> packet timeout -> OnTimeoutPacket -> refundPacketToken -> MintNFT(escrowAddress) -> ErrInvalidDenom (denom not found), violating the invariant that a timed-out IBC transfer must restore the sender's NFT, causing scoped impact: sender permanently loses their NFT with no recourse? Proof idea: write a keeper test that burns a voucher NFT via createOutgoing

### Citations

**File:** proto/chainmain/supply/v1/genesis.proto (L1-10)
```text
syntax =
