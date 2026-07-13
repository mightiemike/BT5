### Title
IBC NFT Transfer Permanently Griefable via Denom Name Pre-Registration — (`File: x/nft-transfer/keeper/packet.go`)

---

### Summary

An unprivileged attacker can permanently block any IBC NFT transfer for a given class/channel pair by pre-registering a denom on the destination chain whose **name** equals the deterministically predictable IBC voucher class ID. When the victim's IBC packet arrives, `processReceivedPacket` calls `IssueDenom`, which internally calls `SetDenom`. `SetDenom` enforces uniqueness on **both** the denom ID and the denom name, but the guard in `processReceivedPacket` only checks `HasDenomID`. The name collision causes `IssueDenom` to return an error, the packet fails with an error acknowledgement, and the victim's NFT is temporarily frozen in escrow until the error ack is relayed back and `refundPacketToken` unescrows it. The attack is repeatable indefinitely at negligible cost.

---

### Finding Description

In `processReceivedPacket` (`x/nft-transfer/keeper/packet.go`), when a new IBC NFT class arrives at the destination chain for the first time, the code guards denom creation with only an ID-existence check:

```go
if !k.nftKeeper.HasDenomID(ctx, voucherClassID) {
    if err := k.nftKeeper.IssueDenom(ctx, voucherClassID, voucherClassID, "", data.ClassUri, escrowAddress); err != nil {
        return err
    }
}
``` [1](#0-0) 

`IssueDenom` delegates to `SetDenom`, which enforces **two independent uniqueness constraints**: one on the denom ID and one on the denom name:

```go
func (k Keeper) SetDenom(ctx sdk.Context, denom types.Denom) error {
    if k.HasDenomID(ctx, denom.Id) {
        return sdkerrors.Wrapf(types.ErrInvalidDenom, "denomID %s has already exists", denom.Id)
    }
    if k.HasDenomNm(ctx, denom.Name) {
        return sdkerrors.Wrapf(types.ErrInvalidDenom, "denomName %s has already exists", denom.Name)
    }
    ...
}
``` [2](#0-1) 

The `voucherClassID` is computed deterministically as `ibc/sha256("nft/<destChannel>/<sourceClassID>")` and is used as **both** the ID and the name when `IssueDenom` is called:

```go
if err := k.nftKeeper.IssueDenom(ctx, voucherClassID, voucherClassID, "", data.ClassUri, escrowAddress); err != nil {
``` [3](#0-2) 

`ValidateDenomName` imposes no format or length restriction — any non-empty string is accepted:

```go
func ValidateDenomName(denomName string) error {
    denomName = strings.TrimSpace(denomName)
    if len(denomName) == 0 {
        return sdkerrors.Wrapf(ErrInvalidDenomName, "denom name(%s) can not be space", denomName)
    }
    return nil
}
``` [4](#0-3) 

An attacker can therefore submit a `MsgIssueDenom` with any valid ID (e.g., `"attack"`) and set `name = voucherClassID` (the 68-character `ibc/...` string). This registers the name in the `KeyDenomName` store. When the victim's IBC packet later arrives, `HasDenomID` returns false (the ID is not yet taken), so the guard passes, but `SetDenom` then hits the `HasDenomNm` check and returns `ErrInvalidDenom`. The error propagates back through `OnRecvPacket`:

```go
if err := im.keeper.OnRecvPacket(ctx, channelVersion, packet, data); err != nil {
    ack = types.NewErrorAcknowledgement(err)
}
``` [5](#0-4) 

The error acknowledgement triggers `refundPacketToken` on the source chain, which unescrows the NFT back to the sender:

```go
case *channeltypes.Acknowledgement_Error:
    return k.refundPacketToken(ctx, packet, data)
``` [6](#0-5) 

During the IBC round trip (source → destination → source), the NFT is held in the escrow account and is inaccessible to the owner.

---

### Impact Explanation

- The victim's NFT is escrowed on the source chain at the moment `SendTransfer` is called.
- The packet fails on the destination chain due to the name collision.
- The NFT is temporarily frozen in the escrow address until the error ack is relayed back and `refundPacketToken` executes.
- The attack can be repeated indefinitely: after each refund, the attacker does nothing further — the poisoned denom name persists on the destination chain, so every future IBC transfer of the same class over the same channel will fail at the same point.
- The corrupted invariant is the NFT escrow: NFTs escrowed for an IBC transfer must either be delivered to the receiver or promptly returned to the sender. The griefing attack forces repeated unnecessary escrow/unescrow cycles.

---

### Likelihood Explanation

The attack requires no privileged access. The attacker needs only to:
1. Know the victim's source denom ID and the IBC channel identifiers (both are public on-chain data).
2. Compute `sha256("nft/<destChannel>/<sourceDenomID>")` — trivial off-chain computation.
3. Submit a single `MsgIssueDenom` transaction on the destination chain with `name = "ibc/<hash>"`.

This can be done proactively (before the victim ever sends) or reactively (after observing a pending IBC transfer in the mempool). Because Cosmos SDK chains have a mempool and IBC relaying is asynchronous (packets are relayed in a separate transaction), there is a reliable window for the attacker to act. The cost is one transaction fee on the destination chain, paid once, and the effect is permanent for that class/channel pair.

---

### Recommendation

In `processReceivedPacket`, replace the `HasDenomID`-only guard with a combined check that also tests `HasDenomNm` before attempting `IssueDenom`:

```go
if !k.nftKeeper.HasDenomID(ctx, voucherClassID) {
    if k.nftKeeper.HasDenomNm(ctx, voucherClassID) {
        // name already taken by a different denom; cannot create IBC voucher class
        return sdkerrors.Wrapf(types.ErrInvalidDenom, "denom name %s already registered", voucherClassID)
    }
    if err := k.nftKeeper.IssueDenom(ctx, voucherClassID, voucherClassID, "", data.ClassUri, escrowAddress); err != nil {
        return err
    }
}
```

Alternatively, decouple the IBC voucher denom name from the IBC voucher denom ID (e.g., use an empty name or a non-conflicting name), so that user-registered denom names cannot collide with IBC-generated denom IDs.

---

### Proof of Concept

**Setup**: Chain A has denom `"mynft"` with a token `"token1"` owned by Alice. An IBC channel exists: `channel-0` on chain A (port `nft`), `channel-0` on chain B (port `nft`).

**Step 1 — Attacker pre-registers the name on chain B**:
```
# Compute voucherClassID = ibc/sha256("nft/channel-0/mynft")
# e.g., voucherClassID = "ibc/AABBCC..."

chain-maind tx nft issue "attackdenom" \
  --name="ibc/AABBCC..." \
  --from=attacker \
  --chain-id=chain-b
```

This succeeds because `ValidateDenomName` accepts any non-empty string, and `"attackdenom"` is a valid denom ID.

**Step 2 — Alice sends her NFT via IBC**:
```
chain-maind tx nft-transfer transfer nft channel-0 <alice-addr-on-B> mynft token1 \
  --from=alice \
  --chain-id=chain-a
```

On chain A, `token1` is transferred to the escrow address. The IBC packet is submitted.

**Step 3 — Packet arrives at chain B**:
`processReceivedPacket` runs:
- `HasDenomID("ibc/AABBCC...")` → `false` (guard passes)
- `IssueDenom("ibc/AABBCC...", "ibc/AABBCC...", ...)` → `SetDenom` → `HasDenomNm("ibc/AABBCC...")` → `true` → **error returned**

**Step 4 — Error ack relayed back to chain A**:
`refundPacketToken` unescrows `token1` back to Alice. Alice's NFT is returned, but she paid fees and experienced a delay.

**Step 5 — Repeat**: Alice retries the transfer. The attack repeats indefinitely because the poisoned denom name on chain B is permanent.

### Citations

**File:** x/nft-transfer/keeper/packet.go (L167-171)
```go
		if !k.nftKeeper.HasDenomID(ctx, voucherClassID) {
			if err := k.nftKeeper.IssueDenom(ctx, voucherClassID, voucherClassID, "", data.ClassUri, escrowAddress); err != nil {
				return err
			}
		}
```

**File:** x/nft/keeper/denom.go (L26-39)
```go
func (k Keeper) SetDenom(ctx sdk.Context, denom types.Denom) error {
	if k.HasDenomID(ctx, denom.Id) {
		return sdkerrors.Wrapf(types.ErrInvalidDenom, "denomID %s has already exists", denom.Id)
	}

	if k.HasDenomNm(ctx, denom.Name) {
		return sdkerrors.Wrapf(types.ErrInvalidDenom, "denomName %s has already exists", denom.Name)
	}

	store := ctx.KVStore(k.storeKey)
	bz := k.cdc.MustMarshal(&denom)
	store.Set(types.KeyDenomID(denom.Id), bz)
	store.Set(types.KeyDenomName(denom.Name), []byte(denom.Id))
	return nil
```

**File:** x/nft/types/validation.go (L57-63)
```go
func ValidateDenomName(denomName string) error {
	denomName = strings.TrimSpace(denomName)
	if len(denomName) == 0 {
		return sdkerrors.Wrapf(ErrInvalidDenomName, "denom name(%s) can not be space", denomName)
	}
	return nil
}
```

**File:** x/nft-transfer/ibc_module.go (L169-171)
```go
		if err := im.keeper.OnRecvPacket(ctx, channelVersion, packet, data); err != nil {
			ack = types.NewErrorAcknowledgement(err)
		}
```

**File:** x/nft-transfer/keeper/relay.go (L119-120)
```go
	case *channeltypes.Acknowledgement_Error:
		return k.refundPacketToken(ctx, packet, data)
```
