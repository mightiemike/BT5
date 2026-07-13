The vulnerability is real and reachable. Let me confirm the complete call chain before writing the finding.

The key facts from the code:

1. **No `TransferAuthorization` type exists** — the glob search for `authz*.go` in `x/nft-transfer/types/` returned nothing. The module only registers `MsgTransfer` as a plain `sdk.Msg`.

2. **`GetSigners()` returns `msg.Sender`** — so in an authz `MsgExec`, the grantee sets `msg.Sender = granter_address`, authz verifies `GetSigners()` matches the granter, and executes.

3. **`createOutgoingPacket` ownership check passes** — `sender.Equals(owner)` at line 102 uses the granter's address as `sender`, which IS the owner of the NFTs.

4. **`TransferOwner(granter, escrow)` executes** — NFTs are moved out of the granter's control.

---

### Title
Unrestricted GenericAuthorization for MsgTransfer Allows Grantee to Drain All NFTs Owned by Granter — (`x/nft-transfer/types/msgs.go`, `x/nft-transfer/keeper/packet.go`)

### Summary
The `nft-transfer` module has no custom `TransferAuthorization` type (unlike ibc-go's fungible token transfer module). A `GenericAuthorization` for `MsgTransfer` carries no classID or tokenID restrictions. A grantee can craft a `MsgExec` wrapping a `MsgTransfer` with `Sender = granter` and arbitrary `ClassId`/`TokenIds`, pass the ownership check in `createOutgoingPacket`, and escrow or burn every NFT the granter owns.

### Finding Description

`MsgTransfer.GetSigners()` returns the address in `msg.Sender`: [1](#0-0) 

When the Cosmos SDK authz module executes a `MsgExec`, it verifies that `GetSigners()` of the inner message matches the granter, then dispatches the message as if the granter signed it. With a `GenericAuthorization` for `TypeMsgTransfer` (`"nft-transfer"`): [2](#0-1) 

there is no restriction on which `ClassId` or `TokenIds` the grantee may specify. The module registers no custom authorization type: [3](#0-2) 

Inside `createOutgoingPacket`, the only ownership guard is: [4](#0-3) 

Because `sender` is the granter's address (derived from `msg.Sender` in `msg_server.Transfer`): [5](#0-4) 

and the granter legitimately owns the NFTs, `sender.Equals(owner)` is `true` and the check passes. The NFT is then escrowed: [6](#0-5) 

or burned (sink-chain path): [7](#0-6) 

permanently removing it from the granter's ownership.

### Impact Explanation
A grantee holding a `GenericAuthorization` for `MsgTransfer` can transfer **any NFT across any classID** owned by the granter to an attacker-controlled receiver on a remote chain, with no on-chain mechanism to limit scope. The granter loses all NFTs; the attacker receives them on the destination chain. This is a direct, irreversible asset loss.

### Likelihood Explanation
Any user who grants `GenericAuthorization` for `MsgTransfer` — a natural action for delegating IBC NFT transfer capability — is fully exposed. The exploit requires only a valid IBC channel and a single `MsgExec` transaction. No privileged access, governance, or key compromise is needed.

### Recommendation
Implement a `TransferAuthorization` type (analogous to ibc-go's `TransferAuthorization` for fungible tokens) that restricts grants to specific `(classID, tokenID)` pairs or at minimum specific classIDs. Register it via `RegisterImplementations` for `authz.Authorization`. Reject `GenericAuthorization` for `TypeMsgTransfer` at grant creation time, or enforce that the authz-resolved signer matches the grantee (not the granter) when classID/tokenID scope is absent.

### Proof of Concept
```
1. Granter owns NFT class="myClass", tokenID="token1"
2. Granter submits MsgGrant:
     granter=<granter_addr>, grantee=<attacker_addr>
     authorization=GenericAuthorization{MsgTypeUrl: "/nft-transfer.MsgTransfer"}
3. Attacker submits MsgExec:
     grantee=<attacker_addr>
     msgs=[MsgTransfer{
       SourcePort: "nft-transfer",
       SourceChannel: "channel-0",
       ClassId: "myClass",
       TokenIds: ["token1"],
       Sender: <granter_addr>,      // attacker sets this
       Receiver: <attacker_remote_addr>,
       ...
     }]
4. authz: GetSigners() -> [granter_addr] matches grant -> dispatches
5. msg_server.Transfer: sender = granter_addr
6. createOutgoingPacket: nft.GetOwner() == granter_addr == sender -> passes
7. TransferOwner(granter_addr, escrowAddress) -> NFT leaves granter
8. IBC packet delivered -> attacker receives NFT on remote chain
```

### Citations

**File:** x/nft-transfer/types/msgs.go (L16-18)
```go
const (
	TypeMsgTransfer = "nft-transfer"
)
```

**File:** x/nft-transfer/types/msgs.go (L91-96)
```go
func (msg MsgTransfer) GetSigners() []sdk.AccAddress {
	signer, err := sdk.AccAddressFromBech32(msg.Sender)
	if err != nil {
		panic(err)
	}
	return []sdk.AccAddress{signer}
```

**File:** x/nft-transfer/types/codec.go (L29-32)
```go
func RegisterInterfaces(registry codectypes.InterfaceRegistry) {
	registry.RegisterImplementations((*sdk.Msg)(nil), &MsgTransfer{})

	msgservice.RegisterMsgServiceDesc(registry, &_Msg_serviceDesc)
```

**File:** x/nft-transfer/keeper/packet.go (L101-104)
```go
		owner := nft.GetOwner()
		if !sender.Equals(owner) {
			return channeltypes.Packet{}, newsdkerrors.Wrap(sdkerrors.ErrUnauthorized, "not token owner")
		}
```

**File:** x/nft-transfer/keeper/packet.go (L106-111)
```go
		if isAwayFromOrigin {
			// create the escrow address for the tokens
			escrowAddress := types.GetEscrowAddress(sourcePort, sourceChannel)
			if err := k.nftKeeper.TransferOwner(ctx, classID, tokenID, sender, escrowAddress); err != nil {
				return channeltypes.Packet{}, err
			}
```

**File:** x/nft-transfer/keeper/packet.go (L113-115)
```go
			// we are sink chain, burn the voucher
			if err := k.nftKeeper.BurnNFTUnverified(ctx, classID, tokenID, sender); err != nil {
				return channeltypes.Packet{}, err
```

**File:** x/nft-transfer/keeper/msg_server.go (L18-24)
```go
	sender, err := sdk.AccAddressFromBech32(msg.Sender)
	if err != nil {
		return nil, err
	}
	if err := k.SendTransfer(
		ctx, msg.SourcePort, msg.SourceChannel, msg.ClassId, msg.TokenIds,
		sender, msg.Receiver, msg.TimeoutHeight, msg.TimeoutTimestamp,
```
