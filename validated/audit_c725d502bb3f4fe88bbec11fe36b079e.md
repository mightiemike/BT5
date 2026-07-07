### Title
Reduce-Only Guard Bypassed When Isolated Subaccount Already Exists, Allowing Unauthorized Margin Transfer — (`core/contracts/OffchainExchange.sol`)

---

### Summary

The reduce-only check in `createIsolatedSubaccount` is gated inside the `newIsolatedSubaccount == bytes32(0)` branch. When an isolated subaccount for the given `productId` already exists, the guard is never evaluated, and the function unconditionally sets `digestToSubaccount[digest]` and transfers margin to the existing isolated subaccount — even when the order carries the reduce-only flag.

---

### Finding Description

In `OffchainExchange.createIsolatedSubaccount`, the function first searches for an existing isolated subaccount for `txn.productId`: [1](#0-0) 

If one is found, `newIsolatedSubaccount` is set to it and the loop breaks. The reduce-only guard is then placed **inside** the `newIsolatedSubaccount == bytes32(0)` branch: [2](#0-1) 

When `newIsolatedSubaccount != bytes32(0)` (existing subaccount found), execution falls through the entire `if` block and reaches the unconditional post-block code: [3](#0-2) 

Both `digestToSubaccount[digest] = newIsolatedSubaccount` and the margin transfer (`spotEngine.updateBalance`) execute regardless of whether the order is reduce-only. The reduce-only flag is never checked on this path.

---

### Impact Explanation

Two concrete state mutations occur that violate protocol invariants:

1. **Unauthorized margin transfer**: `spotEngine.updateBalance` deducts `margin` from `txn.order.sender` and credits it to the existing isolated subaccount. A reduce-only order is supposed to only reduce an existing position — it must never move margin. This transfer changes the collateral distribution between parent and isolated subaccount, affecting health calculations, liquidation thresholds, and the isolated subaccount's ability to sustain or grow a position.

2. **`digestToSubaccount` registration**: The digest of a reduce-only order is registered as an isolated order. When this order is later matched via `matchOrders`, it is routed to the isolated subaccount. This means a reduce-only order can be used to open or increase exposure on the isolated subaccount if the matching engine does not independently re-enforce the reduce-only constraint at fill time.

The combined effect is that a trader can inject margin into an existing isolated subaccount under the guise of a reduce-only order, directly contradicting the invariant that reduce-only orders must never transfer margin or open new positions.

---

### Likelihood Explanation

The path is fully reachable by any trader with a valid signature. No admin, sequencer compromise, or special privilege is required. The attacker only needs to:
- Have an existing isolated subaccount for product P (created normally)
- Submit a second `CreateIsolatedSubaccount` transaction for the same P with reduce-only bit set and `margin > 0` encoded in the appendix

The `onlyEndpoint` modifier means the call must go through the Endpoint, but `CreateIsolatedSubaccount` is a standard signed transaction type processed by the sequencer in the normal offchain order flow — a supported production path.

---

### Recommendation

Move the reduce-only check outside the `newIsolatedSubaccount == bytes32(0)` branch so it applies unconditionally before any state mutation:

```solidity
// Before the if block at line 1040
require(
    !_isReduceOnly(txn.order.appendix),
    "Reduce-only order cannot transfer margin to isolated subaccount"
);
```

Or, if reduce-only orders are intended to be routable to an existing isolated subaccount (without margin transfer), add a separate guard before the margin transfer block:

```solidity
// At line 1074
if (margin > 0) {
    require(!_isReduceOnly(txn.order.appendix), "Reduce-only order cannot transfer margin");
    ...
}
```

---

### Proof of Concept

```solidity
// 1. Create isolated subaccount for product P (normal order, no reduce-only flag)
endpoint.submitTransaction(CreateIsolatedSubaccount({
    productId: P,
    order: Order({ sender: trader, appendix: ISOLATED_FLAG, ... }),
    ...
}));
// isolatedSubaccounts[trader][0] = isoSubaccount_P

// 2. Submit second CreateIsolatedSubaccount for same P with reduce-only flag AND margin > 0
endpoint.submitTransaction(CreateIsolatedSubaccount({
    productId: P,
    order: Order({ sender: trader, appendix: ISOLATED_FLAG | REDUCE_ONLY_FLAG | encodeMargin(100e18), ... }),
    ...
}));

// Assert: margin was transferred (VIOLATION)
assert(spotEngine.getBalance(QUOTE_PRODUCT_ID, isoSubaccount_P) == initialBalance + 100e18);
// Assert: digestToSubaccount was set (VIOLATION)
assert(offchainExchange.digestToSubaccount(digest2) == isoSubaccount_P);
```

The reduce-only guard at line 1042 is never reached because `newIsolatedSubaccount != bytes32(0)` on the second call, so both assertions hold on unmodified code. [4](#0-3)

### Citations

**File:** core/contracts/OffchainExchange.sol (L1025-1038)
```text
        for (uint256 id = 0; (1 << id) <= mask; id += 1) {
            if (mask & (1 << id) != 0) {
                bytes32 subaccount = isolatedSubaccounts[txn.order.sender][id];
                if (subaccount != bytes32(0)) {
                    uint32 productId = RiskHelper.getIsolatedProductId(
                        subaccount
                    );
                    if (productId == txn.productId) {
                        newIsolatedSubaccount = subaccount;
                        break;
                    }
                }
            }
        }
```

**File:** core/contracts/OffchainExchange.sol (L1040-1087)
```text
        if (newIsolatedSubaccount == bytes32(0)) {
            require(
                !_isReduceOnly(txn.order.appendix),
                "Reduce-only order cannot create isolated subaccount"
            );
            require(
                mask != (1 << MAX_ISOLATED_SUBACCOUNTS_PER_ADDRESS) - 1,
                "Too many isolated subaccounts"
            );
            uint8 id = 0;
            while (mask & 1 != 0) {
                mask >>= 1;
                id += 1;
            }

            // |  address | reserved | productId |   id   |  'iso'  |
            // | 20 bytes |  6 bytes |  2 bytes  | 1 byte | 3 bytes |
            newIsolatedSubaccount = bytes32(
                (uint256(uint160(senderAddress)) << 96) |
                    (uint256(txn.productId) << 32) |
                    (uint256(id) << 24) |
                    6910831
            );
            isolatedSubaccountsMask[senderAddress] |= 1 << id;
            parentSubaccounts[newIsolatedSubaccount] = txn.order.sender;
            isolatedSubaccounts[txn.order.sender][id] = newIsolatedSubaccount;
            _onCreateIsolatedSubaccount(
                newIsolatedSubaccount,
                txn.order.sender
            );
        }

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
