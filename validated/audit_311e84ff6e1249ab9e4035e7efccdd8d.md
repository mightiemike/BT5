The code path is concrete and traceable. Let me verify the authz registration and any custom authorization types.

### Title
Authz Grantee Can Redirect Granter's NFT to Grantee-Controlled Address via Unconstrained `MsgTransfer.Receiver` - (`x/nft-transfer/types/msgs.go`, `x/nft-transfer/keeper/packet.go`)

---

### Summary

An authz grantee holding a `GenericAuthorization` for `MsgTransfer` can submit a `MsgTransfer` where `msg.Sender` is the granter (NFT owner) and `msg.Receiver` is any address the grantee controls. Because `GetSigners` returns `msg.Sender` (the granter), authz authorizes the transaction. Because `createOutgoingPacket` checks `sender.Equals(owner)` using the same `msg.Sender` value, the ownership guard passes. The granter's NFT is escrowed and a packet is sent with the grantee's address as receiver, causing the NFT to be delivered to the grantee on the destination chain.

---

### Finding Description

**Step 1 — `GetSigners` returns `msg.Sender`** [1](#0-0) 

The authz middleware resolves the required signer as `msg.Sender` (the granter). It finds the grant from granter → grantee for `MsgTransfer`, authorizes the execution, and dispatches the message.

**Step 2 — `Transfer` passes `msg.Sender` as `sender` to `SendTransfer`** [2](#0-1) 

No check is made that the tx signer equals `msg.Sender`. The grantee signs the outer `MsgExec`; the inner `MsgTransfer.Sender` is the granter.

**Step 3 — `createOutgoingPacket` ownership check passes** [3](#0-2) 

`sender` is derived from `msg.Sender` (the granter), who genuinely owns the NFT. The check `sender.Equals(owner)` succeeds. The NFT is escrowed.

**Step 4 — Packet is constructed with grantee-controlled `receiver`** [4](#0-3) 

`receiver` is taken verbatim from `msg.Receiver`, which the grantee set to their own address. No validation or restriction is applied to this field.

**Step 5 — No custom `Authorization` type exists** [5](#0-4) 

`MsgTransfer` is registered only as a `sdk.Msg`. There is no `TransferAuthorization` equivalent (as exists in ICS-20) that would restrict allowed receiver addresses. `GenericAuthorization` imposes no field-level constraints.

---

### Impact Explanation

A grantee can permanently steal any NFT owned by the granter by redirecting it to a grantee-controlled address on the destination chain. The granter's NFT is escrowed on the source chain and minted/unescrowed to the grantee on the destination chain. The granter has no recourse after the packet is relayed. This is a direct, irreversible loss of an asset the granter never intended to transfer to the grantee.

---

### Likelihood Explanation

Any user who grants `GenericAuthorization` for `MsgTransfer` to another party (e.g., for automation, delegation of transfer rights, or protocol integrations) is immediately vulnerable. The attack requires no privileged access, no governance action, and no special chain state beyond the existence of the grant.

---

### Recommendation

Implement a custom `NFTTransferAuthorization` type (analogous to ICS-20's `TransferAuthorization`) that:
- Restricts the allowed `receiver` addresses, or
- Enforces that `msg.Receiver` must equal the granter's address on the destination chain, or
- Requires the grantee to be the `msg.Sender` (i.e., only self-transfers can be authorized).

Register it via `RegisterImplementations` in `codec.go` so the authz module uses it instead of `GenericAuthorization`.

---

### Proof of Concept

```
1. Granter (addr_A) owns NFT (classID="myclass", tokenID="1") on chain A.
2. Granter grants GenericAuthorization{MsgTypeURL: "/nft.transfer.v1.MsgTransfer"} to grantee (addr_B).
3. Grantee constructs:
     MsgExec{
       Grantee: addr_B,
       Msgs: [MsgTransfer{
         Sender:   addr_A,   // granter = NFT owner
         Receiver: addr_B_on_chain_C,  // grantee-controlled destination
         ClassId:  "myclass",
         TokenIds: ["1"],
         ...
       }]
     }
4. Grantee signs and broadcasts MsgExec.
5. Authz: GetSigners returns addr_A; grant addr_A→addr_B exists; authorized.
6. createOutgoingPacket: sender=addr_A, owner=addr_A → passes; NFT escrowed.
7. Packet relayed to chain C; NFT minted/unescrowed to addr_B_on_chain_C.
8. Granter's NFT is now owned by the grantee on chain C.
```

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
