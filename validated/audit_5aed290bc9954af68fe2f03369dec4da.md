### Title
Stale `digestToSubaccount` Mapping Allows Isolated Orders to Execute Without Margin After Subaccount Closure - (File: `core/contracts/OffchainExchange.sol`)

---

### Summary

`_tryCloseIsolatedSubaccount()` clears the isolated subaccount from all active-tracking mappings but never clears `digestToSubaccount[digest]`. Both `createIsolatedSubaccount()` and `matchOrders()` gate on `digestToSubaccount[digest] != bytes32(0)` — a historical check — rather than verifying the isolated subaccount is currently active. A user can reuse the same signed order after their isolated subaccount has been closed, causing the protocol to route the order to a zero-balance subaccount with no margin transfer, creating an uncovered liability.

---

### Finding Description

When an isolated subaccount is closed via `_tryCloseIsolatedSubaccount()`, the function clears the subaccount from active-tracking state: [1](#0-0) 

```solidity
isolatedSubaccountsMask[addr] &= ~uint256(0) ^ (1 << id);
isolatedSubaccounts[parent][id] = bytes32(0);
parentSubaccounts[subaccount] = bytes32(0);
```

However, `digestToSubaccount[digest]` is **never cleared**. The mapping permanently retains the association between the original order digest and the now-closed isolated subaccount.

`createIsolatedSubaccount()` then uses this stale mapping as a gate: [2](#0-1) 

```solidity
if (digestToSubaccount[digest] != bytes32(0)) {
    return digestToSubaccount[digest];
}
```

This check asks "was this digest *ever* mapped?" not "is the mapped subaccount *currently active*?" When the stale entry is hit, the function returns the closed subaccount immediately — **skipping the entire margin transfer block** at lines 1074–1087.

The same stale check appears in `matchOrders()`: [3](#0-2) 

```solidity
if (digestToSubaccount[ordersInfo.taker.digest] != bytes32(0)) {
    taker.order.sender = digestToSubaccount[ordersInfo.taker.digest];
}
```

This redirects the order sender to the closed (zero-balance) isolated subaccount. The health check that follows always passes: [4](#0-3) 

```solidity
function isHealthy(bytes32 /* subaccount */) internal view virtual returns (bool) {
    return true;
}
```

The fix exists in the codebase but is not applied here. `isIsolatedSubaccountActive()` correctly checks current state: [5](#0-4) 

---

### Impact Explanation

A user whose isolated subaccount has been closed can resubmit the same signed order (same digest, partially unfilled). The sequencer processes it in good faith — the signature is valid, the order is not expired, `filledAmounts[digest]` is below `order.amount`. The contract routes the order to the closed isolated subaccount with zero balance and no margin. The isolated subaccount accumulates a negative quote balance with no collateral backing it. Because `isHealthy()` always returns `true`, no on-chain check catches this. The isolated subaccount is no longer tracked in `isolatedSubaccounts` or `isolatedSubaccountsMask`, so the normal close path cannot recover it. The protocol holds an uncovered liability.

---

### Likelihood Explanation

The trigger is a partially-filled isolated order whose isolated subaccount is subsequently closed before the order is fully filled. This is a normal lifecycle event: a user opens an isolated position, closes it manually (reducing `balance.amount` to zero, triggering `_tryCloseIsolatedSubaccount`), and the original order still has remaining fill capacity. The sequencer processes the resubmitted order because it passes all off-chain validity checks. No privileged access or sequencer compromise is required — the sequencer acts honestly on a structurally valid transaction.

---

### Recommendation

In `createIsolatedSubaccount()`, replace the historical non-zero check with an active-state check using the existing `isIsolatedSubaccountActive()` function:

```solidity
// Before (stale check):
if (digestToSubaccount[digest] != bytes32(0)) {
    return digestToSubaccount[digest];
}

// After (current-state check):
bytes32 existing = digestToSubaccount[digest];
if (existing != bytes32(0) && isIsolatedSubaccountActive(txn.order.sender, existing)) {
    return existing;
}
// else fall through to create a new isolated subaccount (or revert if reduce-only)
```

Additionally, `_tryCloseIsolatedSubaccount()` should clear `digestToSubaccount` for all digests associated with the closed subaccount, or the protocol should maintain a reverse mapping from isolated subaccount to digest for efficient cleanup.

---

### Proof of Concept

1. User signs isolated order `O` (digest `D`, amount 100, margin `M`). Sequencer calls `createIsolatedSubaccount(O)` → ISO created, margin `M` transferred from parent to ISO. `digestToSubaccount[D] = ISO`.
2. Sequencer matches 50 units. `filledAmounts[D] = 50`. ISO holds 50 base units, negative quote.
3. User closes the position via a separate order (ISO.amount → 0). `_tryCloseIsolatedSubaccount` fires: ISO's balance is swept back to parent, `isolatedSubaccounts[parent][id] = bytes32(0)`, `parentSubaccounts[ISO] = bytes32(0)`. **`digestToSubaccount[D]` remains `ISO`.**
4. User resubmits order `O` to the sequencer. Sequencer calls `createIsolatedSubaccount(O)`: line 1009 hits `digestToSubaccount[D] != bytes32(0)` → returns ISO immediately, **no margin transferred**.
5. Sequencer calls `matchOrders` with digest `D`: line 673 redirects `taker.order.sender = ISO`. ISO has zero balance. 50 units are matched. ISO receives 50 base, pays 50×price in quote → ISO quote balance goes negative.
6. `isHealthy(ISO)` returns `true`. Transaction succeeds. ISO holds a negative quote balance with zero margin. Protocol has an uncovered liability equal to 50×price.

### Citations

**File:** core/contracts/OffchainExchange.sol (L202-204)
```text
            isolatedSubaccountsMask[addr] &= ~uint256(0) ^ (1 << id);
            isolatedSubaccounts[parent][id] = bytes32(0);
            parentSubaccounts[subaccount] = bytes32(0);
```

**File:** core/contracts/OffchainExchange.sol (L625-629)
```text
    function isHealthy(
        bytes32 /* subaccount */
    ) internal view virtual returns (bool) {
        return true;
    }
```

**File:** core/contracts/OffchainExchange.sol (L673-675)
```text
        if (digestToSubaccount[ordersInfo.taker.digest] != bytes32(0)) {
            taker.order.sender = digestToSubaccount[ordersInfo.taker.digest];
        }
```

**File:** core/contracts/OffchainExchange.sol (L1009-1011)
```text
        if (digestToSubaccount[digest] != bytes32(0)) {
            return digestToSubaccount[digest];
        }
```

**File:** core/contracts/OffchainExchange.sol (L1118-1129)
```text
    function isIsolatedSubaccountActive(bytes32 parent, bytes32 subaccount)
        external
        view
        returns (bool)
    {
        for (uint256 id = 0; id < MAX_ISOLATED_SUBACCOUNTS_PER_ADDRESS; id++) {
            if (subaccount == isolatedSubaccounts[parent][id]) {
                return true;
            }
        }
        return false;
    }
```
