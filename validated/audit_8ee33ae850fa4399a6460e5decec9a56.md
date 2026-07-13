The code path is clear. Let me verify the NFT keeper's `TransferOwner` and check if any `MsgTransferAuthorization` type exists anywhere in the codebase.

### Title
Authz Grantee Can Redirect Granter's NFT IBC Transfer to Attacker-Controlled Receiver — (`x/nft-transfer/keeper/msg_server.go`, `x/nft-transfer/keeper/packet.go`)

---

### Summary

The `nft-transfer` module's `Keeper.Transfer` derives the sender address directly from the user-supplied `msg.Sender` field without verifying it matches the actual transaction signer. Combined with the absence of any `MsgTransferAuthorization` type, an authz grantee holding a `GenericAuthorization` for `MsgTransfer` can craft a message with `msg.Sender = granter` and `msg.Receiver = attacker_counterparty_address`, pass every on-chain guard, escrow the granter's NFT, and have it delivered to the attacker on the counterparty chain.

---

### Finding Description

**Step 1 — `GetSigners()` returns `msg.Sender` verbatim.**

`GetSigners()` parses and returns `msg.Sender` with no cross-check against the actual transaction signer: [1](#0-0) 

When the attacker submits `MsgExec{ Msgs: [MsgTransfer{Sender: granter, Receiver: attacker, ...}] }`, the SDK authz ante-handler sees `GetSigners() = [granter]`, finds a valid grant from granter → grantee, and proceeds.

**Step 2 — `Keeper.Transfer` blindly trusts `msg.Sender`.** [2](#0-1) 

`sender` is decoded from `msg.Sender` (the granter's address). There is no check that `sender` equals the actual transaction signer. `SendTransfer` is called with this attacker-supplied sender.

**Step 3 — `createOutgoingPacket` owner check passes because the granter owns the NFT.** [3](#0-2) 

`sender` = granter, `owner` = granter (the legitimate NFT owner) → `sender.Equals(owner)` is `true`. The guard is satisfied.

**Step 4 — The packet is constructed with the attacker-controlled `receiver`.** [4](#0-3) 

`receiver` flows directly from `msg.Receiver`, which the attacker set to their own address on the counterparty chain. The granter's NFT is escrowed (or burned) on the source chain and the IBC packet instructs the counterparty to deliver it to the attacker.

**Step 5 — No `MsgTransferAuthorization` type exists.**

A search across the entire repository returns zero matches for `MsgTransferAuthorization` or `TransferAuthorization`. The codec only registers `MsgTransfer` as a plain `sdk.Msg`: [5](#0-4) 

This means the only available authz grant type is `GenericAuthorization`, which imposes no restrictions on `Receiver`, `ClassId`, or `TokenIds`. The granter has no mechanism to scope the grant to specific NFTs or safe receiver addresses.

---

### Impact Explanation

Any authz grantee with a `GenericAuthorization` for `MsgTransfer` can drain every NFT owned by the granter across any IBC channel. The granter's NFT is permanently transferred to the attacker's address on the counterparty chain. On the source chain the NFT is either locked in escrow (source zone) or burned (sink zone); in neither case does the granter recover it unless the packet times out or is rejected — and the attacker controls the receiver on the counterparty, so a successful delivery is the expected outcome.

---

### Likelihood Explanation

The precondition is that the victim has granted `MsgTransfer` authz to the attacker. This is realistic in several production scenarios:

- A dApp or marketplace requests a broad `GenericAuthorization` for `MsgTransfer` to automate cross-chain listings or bridging on behalf of users.
- A user grants authz to a smart-contract-controlled address or a third-party relayer.
- A phishing or social-engineering flow tricks the user into granting authz.

Once the grant exists, the exploit is a single `MsgExec` transaction requiring no further cooperation from the victim.

---

### Recommendation

1. **Implement `MsgTransferAuthorization`** analogous to ibc-go's `TransferAuthorization` for fungible tokens. It should allow the granter to restrict: allowed source channels, allowed class IDs, allowed token IDs, and optionally an allowlist of permitted receiver addresses.
2. **Register it** in `RegisterInterfaces` so the authz module resolves it instead of falling back to `GenericAuthorization`.
3. Optionally, add a runtime check in `Keeper.Transfer` that the `msg.Sender` address matches the signer extracted from the SDK context when the message is not executed via authz, as a defense-in-depth measure.

---

### Proof of Concept

```
// Setup
victim  := "cro1victim..."   // owns NFT (classID="myClass", tokenID="token1")
attacker := "cro1attacker..." // grantee

// 1. Victim (or attacker via social engineering) submits:
MsgGrant{
    Granter: victim,
    Grantee: attacker,
    Grant: GenericAuthorization{ MsgTypeURL: "/chainmain.nft_transfer.v1.MsgTransfer" },
}

// 2. Attacker submits:
MsgExec{
    Grantee: attacker,
    Msgs: [
        MsgTransfer{
            SourcePort:    "nft-transfer",
            SourceChannel: "channel-0",
            ClassId:       "myClass",
            TokenIds:      ["token1"],
            Sender:        victim,          // granter's address → GetSigners() = [victim]
            Receiver:      "counterparty1attacker...", // attacker's address on counterparty
            TimeoutTimestamp: <valid>,
        }
    ]
}

// Result:
// - authz check: grantee=attacker has GenericAuthorization from victim for MsgTransfer → PASS
// - Transfer(): sender = victim (decoded from msg.Sender)
// - createOutgoingPacket(): sender.Equals(nft.GetOwner()) → victim == victim → PASS
// - NFT escrowed from victim to escrow address on source chain
// - IBC packet receiver = "counterparty1attacker..."
// - Counterparty chain mints/unescrows NFT to attacker
// - Victim's NFT is permanently stolen
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

**File:** x/nft-transfer/keeper/msg_server.go (L15-27)
```go
func (k Keeper) Transfer(goCtx context.Context, msg *types.MsgTransfer) (*types.MsgTransferResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	sender, err := sdk.AccAddressFromBech32(msg.Sender)
	if err != nil {
		return nil, err
	}
	if err := k.SendTransfer(
		ctx, msg.SourcePort, msg.SourceChannel, msg.ClassId, msg.TokenIds,
		sender, msg.Receiver, msg.TimeoutHeight, msg.TimeoutTimestamp,
	); err != nil {
		return nil, err
	}
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
