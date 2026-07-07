### Title
`digestToSubaccount` Not Cleared on Isolated Subaccount Close Enables Ghost Subaccount Reuse — (File: `core/contracts/OffchainExchange.sol`)

---

### Summary
When `_tryCloseIsolatedSubaccount` closes an isolated subaccount, it clears the registration state (`parentSubaccounts`, `isolatedSubaccounts`, `isolatedSubaccountsMask`) but never clears `digestToSubaccount[digest]`. This leaves a stale digest-to-subaccount mapping pointing at a now-closed "ghost" subaccount. A subsequent submission of the same order digest returns the ghost subaccount via an early-return path that bypasses signature verification and margin transfer, desynchronizing the system's ownership state — a direct structural analog to the `buyoutLien` collateral state hash omission.

---

### Finding Description

**Root cause — missing state update in `_tryCloseIsolatedSubaccount`:**

`_tryCloseIsolatedSubaccount` (OffchainExchange.sol lines 160–208) closes an isolated subaccount by:

- Transferring `vQuoteBalance` back to the parent [1](#0-0) 
- Transferring `quoteBalance` back to the parent [2](#0-1) 
- Clearing `isolatedSubaccountsMask[addr]`, `isolatedSubaccounts[parent][id]`, and `parentSubaccounts[subaccount]` [3](#0-2) 

**What is never cleared:** `digestToSubaccount[digest]` and `digestToMargin[digest]` — both set at subaccount creation time — are never touched by `_tryCloseIsolatedSubaccount`. The function only receives `bytes32 subaccount` and has no access to the originating digest, so the stale mapping persists indefinitely after close. [4](#0-3) 

**How the stale mapping is exploited — early return in `createIsolatedSubaccount`:**

```solidity
if (digestToSubaccount[digest] != bytes32(0)) {
    return digestToSubaccount[digest];   // ← returns ghost subaccount
}
```

This early return fires before signature verification, before margin transfer, and before any registration check. [5](#0-4) 

When the same order digest is re-submitted after the subaccount has been closed, `createIsolatedSubaccount` returns the ghost subaccount. The ghost subaccount has:
- `parentSubaccounts[ghost] = bytes32(0)` (cleared by close)
- No margin (already returned to parent)
- Not present in `isolatedSubaccounts` or `isolatedSubaccountsMask`

The margin that was set at creation time (`digestToMargin[digest] = margin`) is also never cleared, leaving a second stale accounting entry. [6](#0-5) 

---

### Impact Explanation

An order matched against the ghost subaccount opens a position on a subaccount that:
1. Has no registered parent — `getParentSubaccount` returns `bytes32(0)`, bypassing parent-based health and authorization checks (e.g., the `transferQuote` parent-equality check in `Clearinghouse.sol`). [7](#0-6) 
2. Has no margin backing the position — margin was returned to the parent at close time, but the ghost subaccount is returned without re-transferring margin.
3. Is invisible to `isIsolatedSubaccountActive` — the mask bit was cleared, so active-subaccount guards do not apply.

The net result is a position opened in the perp or spot engine with zero margin and no accountable parent, corrupting the protocol's solvency accounting. This is the exact analog of the `buyoutLien` bug: the asset transfer (margin return) completes, but the ownership state (`digestToSubaccount`) is not updated, leaving the system in a desynchronized state where the digest still "owns" a closed subaccount.

---

### Likelihood Explanation

**Medium.** The attack requires re-submitting the same order digest after the subaccount is closed. This is reachable via:
- **Slow mode** — the Endpoint exposes a slow-mode path for censorship resistance that allows users to submit transactions directly without sequencer cooperation. If `CreateIsolatedSubaccount` is a supported slow-mode transaction type, a user can replay the original order after closing the subaccount.
- **Sequencer retry** — the `digestToSubaccount` idempotency check exists precisely because the sequencer may submit the same order more than once; a closed-then-replayed order is a realistic sequencer behavior.

No privileged access, governance capture, or key compromise is required.

---

### Recommendation

When `_tryCloseIsolatedSubaccount` closes a subaccount, it must also clear the digest-based state. Since the function only receives `bytes32 subaccount` and not the digest, the fix requires one of:

1. **Store the digest on the subaccount at creation time** — add a `mapping(bytes32 => bytes32) subaccountToDigest` populated in `createIsolatedSubaccount`, and use it in `_tryCloseIsolatedSubaccount` to clear both `digestToSubaccount[digest]` and `digestToMargin[digest]`.
2. **Clear `digestToSubaccount` in `createIsolatedSubaccount` before returning the ghost** — add a liveness check: if `digestToSubaccount[digest]` points to a subaccount whose `parentSubaccounts` entry is `bytes32(0)` (i.e., already closed), treat it as non-existent and proceed with full creation logic.

---

### Proof of Concept

```
1. User calls CreateIsolatedSubaccount with order O1 (digest D1), margin M.
      → digestToSubaccount[D1] = isolatedSub
      → digestToMargin[D1]     = M
      → M transferred: parent → isolatedSub (spot engine)

2. User closes position on isolatedSub.
      → _tryCloseIsolatedSubaccount(isolatedSub) fires:
           quoteBalance M returned: isolatedSub → parent
           parentSubaccounts[isolatedSub]    = bytes32(0)   ✓ cleared
           isolatedSubaccounts[parent][id]   = bytes32(0)   ✓ cleared
           isolatedSubaccountsMask[addr] bit = 0            ✓ cleared
           digestToSubaccount[D1]            = isolatedSub  ✗ NOT cleared
           digestToMargin[D1]                = M            ✗ NOT cleared

3. User re-submits order O1 (same digest D1) via slow mode.
      → createIsolatedSubaccount called:
           digest = D1
           digestToSubaccount[D1] != bytes32(0)  → early return isolatedSub
           (signature check skipped, margin transfer skipped)

4. Order is matched against isolatedSub (ghost):
      → parentSubaccounts[isolatedSub] = bytes32(0)  → no parent
      → no margin in isolatedSub
      → position opened in perp engine with zero margin backing
      → parent health checks that rely on getParentSubaccount bypass
```

### Citations

**File:** core/contracts/OffchainExchange.sol (L160-162)
```text
    function _tryCloseIsolatedSubaccount(bytes32 subaccount) internal {
        uint32 productId = RiskHelper.getIsolatedProductId(subaccount);
        if (productId == 0) {
```

**File:** core/contracts/OffchainExchange.sol (L173-186)
```text
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
```

**File:** core/contracts/OffchainExchange.sol (L187-201)
```text
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
            }
```

**File:** core/contracts/OffchainExchange.sol (L202-204)
```text
            isolatedSubaccountsMask[addr] &= ~uint256(0) ^ (1 << id);
            isolatedSubaccounts[parent][id] = bytes32(0);
            parentSubaccounts[subaccount] = bytes32(0);
```

**File:** core/contracts/OffchainExchange.sol (L1009-1011)
```text
        if (digestToSubaccount[digest] != bytes32(0)) {
            return digestToSubaccount[digest];
        }
```

**File:** core/contracts/OffchainExchange.sol (L1074-1087)
```text
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

**File:** core/contracts/Clearinghouse.sol (L228-235)
```text
        if (RiskHelper.isIsolatedSubaccount(txn.sender)) {
            // isolated subaccounts can only transfer quote back to parent
            require(
                IOffchainExchange(offchainExchange).getParentSubaccount(
                    txn.sender
                ) == txn.recipient,
                ERR_UNAUTHORIZED
            );
```
