### Title
Unrestricted `GenericAuthorization` for `MsgTransfer` Allows Authz Grantee to Steal Granter's NFTs — (`x/nft-transfer/types/msgs.go`, `x/nft-transfer/keeper/packet.go`)

---

### Summary

The `nft-transfer` module implements no custom `Authorization` type for `MsgTransfer`. Any `GenericAuthorization` grant for `MsgTransfer` is therefore completely unrestricted: the grantee can set an arbitrary `Receiver` and arbitrary `TokenIds`, causing the granter's NFTs to be transferred to the attacker without per-transfer consent.

---

### Finding Description

**`GetSigners` and the authz flow**

`MsgTransfer.GetSigners()` returns the address parsed from `msg.Sender`: [1](#0-0) 

When an authz grantee submits `MsgExec([MsgTransfer{Sender=victim, Receiver=attacker, TokenIds=[victim_nft]}])`, the Cosmos SDK authz ante handler:
1. Verifies the **grantee's** signature on the outer `MsgExec`.
2. Looks up a grant from `granter=victim` to `grantee=attacker` for message type `/nft-transfer.MsgTransfer`.
3. Calls `Authorization.Accept()` — for `GenericAuthorization` this always returns `true` with no field-level restrictions.
4. Dispatches the inner `MsgTransfer` with `msg.Sender = victim`.

**Keeper ownership check**

`msg_server.Transfer` derives `sender` directly from `msg.Sender` and passes it to `SendTransfer` → `createOutgoingPacket`: [2](#0-1) 

Inside `createOutgoingPacket`, the only guard is: [3](#0-2) 

Because `sender` is the granter (who legitimately owns the NFT), this check passes. The NFT is then escrowed or burned, and the IBC packet is sent with `receiver = attacker`: [4](#0-3) 

**No custom Authorization type**

`codec.go` registers `MsgTransfer` only as an `sdk.Msg` implementation; no custom `Authorization` interface (analogous to `TransferAuthorization` in ibc-go's fungible token module) is registered: [5](#0-4) 

This means the only usable authz grant type is `GenericAuthorization`, which imposes zero restrictions on `Receiver`, `TokenIds`, `ClassId`, or channel.

---

### Impact Explanation

A grantee holding a `GenericAuthorization` for `MsgTransfer` from a victim can:
- Transfer **any** NFT owned by the victim to **any** address on **any** IBC-connected chain.
- The victim receives no NFT back (it is escrowed or burned on the source chain).
- The attacker receives the NFT on the destination chain via `processReceivedPacket`.

This is direct, irreversible theft of economically valuable NFTs.

---

### Likelihood Explanation

`GenericAuthorization` grants for `MsgTransfer` are a realistic user action: NFT marketplaces, automated bots, and multi-sig setups routinely use authz. A malicious or compromised grantee (e.g., a marketplace contract that turns adversarial) can immediately exploit this. No governance or privileged role is required beyond holding the grant.

---

### Recommendation

Implement a custom `NFTTransferAuthorization` type (analogous to `TransferAuthorization` in ibc-go) that:
- Restricts the allowed `Receiver` addresses and/or `ClassId`/`TokenIds`.
- Is registered via `RegisterInterfaces` so it can be used in place of `GenericAuthorization`.
- Has `Accept()` validate that the submitted `MsgTransfer` fields match the grant's constraints before dispatching.

---

### Proof of Concept

```
1. victim grants: MsgGrant{Granter=victim, Grantee=attacker,
       Grant={Authorization=GenericAuthorization{MsgTypeUrl="/nft-transfer.MsgTransfer"}}}

2. attacker submits: MsgExec{Grantee=attacker,
       Msgs=[MsgTransfer{Sender=victim, Receiver=attacker_dest,
                         ClassId=X, TokenIds=[victim_nft],
                         SourcePort=..., SourceChannel=...}]}

3. authz module: grant found, GenericAuthorization.Accept() → allowed
4. keeper: sender=victim, owner=victim → check passes
5. NFT escrowed under IBC escrow address
6. IBC packet relayed to destination chain
7. destination chain: processReceivedPacket mints/unescrows NFT to attacker_dest
```

State delta: `victim` loses `victim_nft`; `attacker_dest` gains it. No per-transfer consent from victim was required.

### Citations

**File:** x/nft-transfer/types/msgs.go (L91-96)
```go
func (msg MsgTransfer) GetSigners() []sdk.AccAddress {
	signer, err := sdk.AccAddressFromBech32(msg.Sender)
	if err != nil {
		panic(err)
	}
	return []sdk.AccAddress{signer}
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

**File:** x/nft-transfer/keeper/packet.go (L101-104)
```go
		owner := nft.GetOwner()
		if !sender.Equals(owner) {
			return channeltypes.Packet{}, newsdkerrors.Wrap(sdkerrors.ErrUnauthorized, "not token owner")
		}
```

**File:** x/nft-transfer/keeper/packet.go (L120-122)
```go
	packetData := types.NewNonFungibleTokenPacketData(
		fullClassPath, denom.Uri, tokenIDs, tokenURIs, sender.String(), receiver,
	)
```

**File:** x/nft-transfer/types/codec.go (L29-32)
```go
func RegisterInterfaces(registry codectypes.InterfaceRegistry) {
	registry.RegisterImplementations((*sdk.Msg)(nil), &MsgTransfer{})

	msgservice.RegisterMsgServiceDesc(registry, &_Msg_serviceDesc)
```
