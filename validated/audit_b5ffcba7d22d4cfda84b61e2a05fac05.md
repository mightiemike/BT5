### Title
Attacker Can Permanently DOS IBC NFT Class Reception by Pre-Registering the Predictable Denom Name — (`x/nft-transfer/keeper/packet.go`)

---

### Summary

`processReceivedPacket` derives a deterministic `voucherClassID` from fully public information and then calls `IssueDenom` with that value as **both** the denom ID and the denom name. Because the NFT keeper enforces global uniqueness on denom names independently of denom IDs, an attacker can pre-register any denom whose **name** equals the predicted `voucherClassID`. This causes `IssueDenom` to fail with a name-collision error every time the IBC packet is processed, permanently blocking NFT transfers for that class on that channel.

---

### Finding Description

**Step 1 — Predictable `voucherClassID`.**

In `processReceivedPacket`, the voucher class ID is computed deterministically:

```
classPrefix     = destPort + "/" + destChannel + "/"
prefixedClassID = classPrefix + data.ClassId
voucherClassID  = "ibc/" + hex(sha256(prefixedClassID))
```

All three inputs (`destPort`, `destChannel`, `data.ClassId`) are public on-chain values. Any observer can compute `voucherClassID` before a transfer is ever attempted. [1](#0-0) [2](#0-1) 

**Step 2 — `IssueDenom` uses `voucherClassID` as both ID and name.**

```go
if !k.nftKeeper.HasDenomID(ctx, voucherClassID) {
    if err := k.nftKeeper.IssueDenom(ctx, voucherClassID, voucherClassID, "", data.ClassUri, escrowAddress); err != nil {
        return err
    }
}
```

The second argument is the denom **name**, set equal to `voucherClassID`. [3](#0-2) 

**Step 3 — The NFT keeper enforces global name uniqueness.**

`SetDenom` (called inside `IssueDenom`) independently checks both the ID and the name:

```go
if k.HasDenomNm(ctx, denom.Name) {
    return sdkerrors.Wrapf(types.ErrInvalidDenom, "denomName %s has already exists", denom.Name)
}
``` [4](#0-3) 

**Step 4 — The attack.**

An attacker submits `MsgIssueDenom` with:
- **ID** = any arbitrary string (e.g., `"griefing-denom"`)
- **Name** = the pre-computed `voucherClassID` (e.g., `"ibc/ABCDEF..."`)

When the IBC packet later arrives:
1. `HasDenomID(voucherClassID)` returns **false** (the attacker used a different ID), so the guard is bypassed.
2. `IssueDenom(ctx, voucherClassID, voucherClassID, ...)` is called.
3. Inside `SetDenom`, `HasDenomNm(voucherClassID)` returns **true** (attacker's denom owns that name).
4. `SetDenom` returns `ErrInvalidDenom`, `processReceivedPacket` returns an error, and the packet fails. [4](#0-3) [5](#0-4) 

The attacker only needs to execute this **once per (channel, classID) pair**. Because `voucherClassID` is a deterministic hash, it never changes regardless of how many times the transfer is retried.

---

### Impact Explanation

Every IBC NFT transfer of a targeted class over a targeted channel to this chain will permanently fail at packet reception. The source chain will receive an error acknowledgement and refund the NFT to the sender, but the transfer path is irrecoverably blocked. Any protocol or application relying on cross-chain NFT movement for that class is rendered non-functional on that channel. The attacker pays only the gas cost of a single `MsgIssueDenom` transaction.

---

### Likelihood Explanation

All inputs required to compute `voucherClassID` (`destPort`, `destChannel`, `classID`) are publicly visible on-chain before any transfer is submitted. The attacker does not need to frontrun a mempool transaction — they can pre-register the denom at any time before the first transfer attempt. The cost is a single cheap transaction. No special privileges are required.

---

### Recommendation

Decouple the denom name used for IBC-created classes from the `voucherClassID` string, or use a namespace prefix that is unreachable by user-submitted `MsgIssueDenom` transactions (e.g., enforce that user-created denom names cannot start with `"ibc/"`). Alternatively, validate in `processReceivedPacket` that if a denom with `voucherClassID` as its name already exists but was not created by the escrow address, the packet should be rejected with a clear error rather than silently failing inside `IssueDenom`. [4](#0-3) [6](#0-5) 

---

### Proof of Concept

1. Chain A holds NFT class `"myNFT"`. Alice wants to transfer it to chain B via `nft-transfer/channel-0`.
2. Attacker computes on chain B:
   ```
   prefixedClassID = "nft-transfer/channel-0/myNFT"
   voucherClassID  = "ibc/" + hex(sha256("nft-transfer/channel-0/myNFT"))
   ```
3. Attacker submits `MsgIssueDenom{Id: "x", Name: voucherClassID}` on chain B. This succeeds because no denom with that name exists yet.
4. Alice submits `MsgTransfer` on chain A. The IBC packet is relayed to chain B.
5. Chain B's `OnRecvPacket` → `processReceivedPacket`:
   - `HasDenomID(voucherClassID)` → `false` (attacker used ID `"x"`, not `voucherClassID`)
   - `IssueDenom(ctx, voucherClassID, voucherClassID, ...)` is called
   - `SetDenom` → `HasDenomNm(voucherClassID)` → `true` → returns `ErrInvalidDenom`
   - `processReceivedPacket` returns error; packet write-acknowledgement carries the error
6. Chain A's `OnAcknowledgementPacket` refunds Alice's NFT. Alice can never transfer `"myNFT"` to chain B via `channel-0`. [7](#0-6) [4](#0-3) [8](#0-7)

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

**File:** x/nft-transfer/types/trace.go (L31-34)
```go
// GetClassPrefix returns the receiving class prefix
func GetClassPrefix(portID, channelID string) string {
	return fmt.Sprintf("%s/%s/", portID, channelID)
}
```

**File:** x/nft-transfer/types/trace.go (L92-107)
```go
// Hash returns the hex bytes of the SHA256 hash of the ClassTrace fields using the following formula:
//
// hash = sha256(tracePath + "/" + baseClassId)
func (ct ClassTrace) Hash() tmbytes.HexBytes {
	hash := sha256.Sum256([]byte(ct.GetFullClassPath()))
	return hash[:]
}

// IBCClassID a classID for an ICS721 non-fungible token in the format
// 'ibc/{hash(tracePath + BaseClassId)}'. If the trace is empty, it will return the base classID.
func (ct ClassTrace) IBCClassID() string {
	if ct.Path != "" {
		return fmt.Sprintf("%s/%s", ClassPrefix, ct.Hash())
	}
	return ct.BaseClassId
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
