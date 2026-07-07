### Title
Closed Isolated Subaccount Retains Stale `digestToSubaccount` Mapping, Permanently Locking Matched Funds — (`core/contracts/OffchainExchange.sol`)

---

### Summary

`_tryCloseIsolatedSubaccount` clears the parent/mask/slot mappings for a closed isolated subaccount but never clears `digestToSubaccount[digest]`. After closure, any subsequent match of the original order's remaining unfilled amount routes balance updates into the dead isolated subaccount, where they are permanently unrecoverable.

---

### Finding Description

When an isolated order is created, `createIsolatedSubaccount` stores the mapping `digestToSubaccount[digest] = newIsolatedSubaccount` and transfers margin from the parent subaccount. [1](#0-0) 

When the isolated subaccount is later closed (e.g., after liquidation finalization), `_tryCloseIsolatedSubaccount` clears three mappings: [2](#0-1) 

However, `digestToSubaccount[digest]` is **never cleared**. In `matchOrders`, before every fill, the sender is unconditionally overwritten with whatever `digestToSubaccount` returns: [3](#0-2) 

This means that after the isolated subaccount is closed, any remaining unfilled portion of the original order will still route balance updates to the now-dead isolated subaccount. `_validateOrder` has no check for whether the mapped isolated subaccount is still active: [4](#0-3) 

Once funds land in the closed isolated subaccount, they cannot be recovered:

- `withdrawCollateral` blocks all isolated subaccounts unconditionally: [5](#0-4) 
- `transferQuote` requires `parentSubaccounts[subaccount] == txn.recipient`, but `parentSubaccounts` was cleared to `bytes32(0)` during closure: [6](#0-5) 
- If `_tryCloseIsolatedSubaccount` is ever called again on the dead subaccount, `parent = bytes32(0)`, so any quote balance would be credited to the zero-address subaccount — effectively burned: [7](#0-6) 

---

### Impact Explanation

A user who placed an isolated perp order that was only partially filled, and whose position was subsequently liquidated and finalized, will have the remaining unfilled order amount matched into a dead isolated subaccount. The resulting quote or base token balance is permanently locked with no recovery path. This is a direct, irreversible loss of user funds.

---

### Likelihood Explanation

The scenario requires: (1) a partially filled isolated order, (2) the resulting position being liquidated to zero triggering `_finalizeSubaccount` → `tryCloseIsolatedSubaccount`, and (3) the sequencer continuing to match the remaining order amount. All three steps are normal protocol operations. The sequencer has no on-chain signal to stop matching the order after the isolated subaccount is closed, since `_validateOrder` does not check subaccount liveness. Likelihood is **medium**.

---

### Recommendation

In `_tryCloseIsolatedSubaccount`, after clearing the parent/mask/slot mappings, also clear the digest mapping. Since the isolated subaccount address encodes the product ID and owner address, the reverse lookup from subaccount to digest is not directly available. The cleanest fix is to store a `subaccountToDigest` reverse mapping at creation time and use it during closure:

```solidity
// In createIsolatedSubaccount:
subaccountToDigest[newIsolatedSubaccount] = digest;
digestToSubaccount[digest] = newIsolatedSubaccount;

// In _tryCloseIsolatedSubaccount:
bytes32 digest = subaccountToDigest[subaccount];
if (digest != bytes32(0)) {
    delete digestToSubaccount[digest];
    delete subaccountToDigest[subaccount];
}
```

Alternatively, add a liveness check in `matchOrders` before overwriting `order.sender`:

```solidity
if (digestToSubaccount[ordersInfo.taker.digest] != bytes32(0)) {
    bytes32 iso = digestToSubaccount[ordersInfo.taker.digest];
    require(
        IOffchainExchange(address(this)).getParentSubaccount(iso) != bytes32(0),
        ERR_INVALID_TAKER
    );
    taker.order.sender = iso;
}
```

---

### Proof of Concept

1. User A submits an isolated perp order (digest `D`) for amount `100`, with margin `M`. `createIsolatedSubaccount` creates isolated subaccount `S`, transfers `M` from parent, sets `digestToSubaccount[D] = S`.
2. Sequencer matches `50` of the order. `S` now holds a perp position of `50`.
3. Price moves adversely. Liquidator calls `liquidateSubaccountImpl` on `S`. `_finalizeSubaccount` returns `true` (position zeroed), `tryCloseIsolatedSubaccount(S)` is called. `parentSubaccounts[S]`, `isolatedSubaccounts[parent][id]`, and `isolatedSubaccountsMask` are cleared. Margin is returned to parent. **`digestToSubaccount[D]` is NOT cleared — still equals `S`.**
4. Sequencer matches the remaining `50` of order `D`. In `matchOrders`: `taker.order.sender = digestToSubaccount[D] = S`. `_updateBalances` credits quote tokens to `S`.
5. User A attempts to recover funds: `withdrawCollateral` reverts (`isIsolatedSubaccount` check). `transferQuote` reverts (`parentSubaccounts[S] == bytes32(0)`). Funds in `S` are permanently locked.

### Citations

**File:** core/contracts/OffchainExchange.sol (L172-200)
```text
            bytes32 parent = parentSubaccounts[subaccount];
            if (balance.vQuoteBalance != 0) {
                perpEngine.updateBalance(
                    productId,
                    subaccount,
                    0,
                    -balance.vQuoteBalance
                );
                perpEngine.updateBalance(
                    productId,
                    parent,
                    0,
                    balance.vQuoteBalance
                );
            }
            int128 quoteBalance = spotEngine
                .getBalance(QUOTE_PRODUCT_ID, subaccount)
                .amount;
            if (quoteBalance != 0) {
                spotEngine.updateBalance(
                    QUOTE_PRODUCT_ID,
                    subaccount,
                    -quoteBalance
                );
                spotEngine.updateBalance(
                    QUOTE_PRODUCT_ID,
                    parent,
                    quoteBalance
                );
```

**File:** core/contracts/OffchainExchange.sol (L202-204)
```text
            isolatedSubaccountsMask[addr] &= ~uint256(0) ^ (1 << id);
            isolatedSubaccounts[parent][id] = bytes32(0);
            parentSubaccounts[subaccount] = bytes32(0);
```

**File:** core/contracts/OffchainExchange.sol (L228-235)
```text
                    subaccount,
                    baseDelta,
                    quoteDelta
                );
            } else {
                callState.spot.updateBalance(
                    callState.productId,
                    subaccount,
```

**File:** core/contracts/OffchainExchange.sol (L410-469)
```text
    function _validateOrder(
        CallState memory callState,
        MarketInfo memory,
        IEndpoint.SignedOrder memory signedOrder,
        bytes32 orderDigest,
        bool isTaker,
        address linkedSigner
    ) internal view returns (bool) {
        if ((signedOrder.order.appendix & 255) != orderVersion()) {
            return false;
        }
        if (signedOrder.order.sender == X_ACCOUNT) {
            return true;
        }
        IEndpoint.Order memory order = signedOrder.order;
        if (isTaker) {
            if (_isMakerOnly(order.appendix)) {
                return false;
            }
        } else {
            if (_isTakerOnly(order.appendix)) {
                return false;
            }
        }

        int128 filledAmount = filledAmounts[orderDigest];
        order.amount -= filledAmount;

        if (_isReduceOnly(order.appendix)) {
            int128 amount = callState.isPerp
                ? callState
                    .perp
                    .getBalance(callState.productId, order.sender)
                    .amount
                : callState
                    .spot
                    .getBalance(callState.productId, order.sender)
                    .amount;
            if ((order.amount > 0) == (amount > 0)) {
                order.amount = 0;
            } else if (order.amount > 0) {
                order.amount = MathHelper.min(order.amount, -amount);
            } else if (order.amount < 0) {
                order.amount = MathHelper.max(order.amount, -amount);
            }
        }

        return
            ((order.priceX18 > 0) || _isTWAP(order.appendix)) &&
            (signedOrder.order.sender == N_ACCOUNT ||
                _checkSignature(
                    order.sender,
                    orderDigest,
                    linkedSigner,
                    signedOrder.signature
                )) &&
            // valid amount
            (order.amount != 0) &&
            !_expired(order.expiration);
    }
```

**File:** core/contracts/OffchainExchange.sol (L673-675)
```text
        if (digestToSubaccount[ordersInfo.taker.digest] != bytes32(0)) {
            taker.order.sender = digestToSubaccount[ordersInfo.taker.digest];
        }
```

**File:** core/contracts/OffchainExchange.sol (L1072-1087)
```text
        digestToSubaccount[digest] = newIsolatedSubaccount;

        int128 margin = int128(_isolatedMargin(txn.order.appendix));
        if (margin > 0) {
            digestToMargin[digest] = margin;
            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                txn.order.sender,
                -margin
            );
            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                newIsolatedSubaccount,
                margin
            );
        }
```

**File:** core/contracts/Clearinghouse.sol (L398-398)
```text
        require(!RiskHelper.isIsolatedSubaccount(sender), ERR_UNAUTHORIZED);
```
