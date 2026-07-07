### Title
Zero-Margin Isolated Subaccount Creation Bypasses Collateral Invariant — (`core/contracts/OffchainExchange.sol`)

---

### Summary

`createIsolatedSubaccount` silently succeeds when `_isolatedMargin` returns 0, creating a fully registered isolated subaccount with no quote balance. Because `isHealthy` unconditionally returns `true` in the base contract, a subsequent `matchOrders` call against that isolated subaccount opens a leveraged perp position with zero collateral and no on-chain health rejection.

---

### Finding Description

**Step 1 — `_isolatedMargin` returns 0 when bits [127:64] are zero.** [1](#0-0) 

```solidity
function _isolatedMargin(uint128 appendix) internal pure returns (uint128) {
    return (appendix >> 64) * (10**12);
}
```

If the upper 64 bits of `appendix` are all zero, the function returns `0`. The isolated-flag bit (bit 8) is independent of those bits, so `_isIsolated` can return `true` while `_isolatedMargin` returns `0`. [2](#0-1) 

**Step 2 — `createIsolatedSubaccount` skips the balance transfer instead of reverting.** [3](#0-2) 

```solidity
int128 margin = int128(_isolatedMargin(txn.order.appendix));
if (margin > 0) {          // ← silent skip, not a revert
    digestToMargin[digest] = margin;
    spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.order.sender, -margin);
    spotEngine.updateBalance(QUOTE_PRODUCT_ID, newIsolatedSubaccount, margin);
}
```

When `margin == 0` the function still:
- registers `newIsolatedSubaccount` in `isolatedSubaccounts` / `isolatedSubaccountsMask`
- sets `parentSubaccounts[newIsolatedSubaccount]`
- sets `digestToSubaccount[digest] = newIsolatedSubaccount`

…but transfers **zero** quote to the isolated subaccount. [4](#0-3) 

**Step 3 — `matchOrders` redirects the order to the zero-balance isolated subaccount.** [5](#0-4) 

```solidity
if (digestToSubaccount[ordersInfo.taker.digest] != bytes32(0)) {
    taker.order.sender = digestToSubaccount[ordersInfo.taker.digest];
}
```

The same order digest used in `createIsolatedSubaccount` is reused in `matchOrders`. The sender is silently replaced with the isolated subaccount (zero quote balance).

**Step 4 — `isHealthy` is a no-op; the post-trade health check always passes.** [6](#0-5) 

```solidity
function isHealthy(bytes32 /* subaccount */) internal view virtual returns (bool) {
    return true;
}
```

After `_updateBalances` credits the isolated subaccount with a perp position and debits its (already-zero) vQuote balance, the health check at lines 826–827 trivially passes. [7](#0-6) 

---

### Impact Explanation

An isolated subaccount is created with `spotEngine.getBalance(QUOTE_PRODUCT_ID, isolatedSubaccount).amount == 0`. After `matchOrders` executes, the isolated subaccount holds a leveraged perp position backed by no collateral. The protocol's solvency invariant — every isolated position is fully backed by transferred margin — is broken at the smart-contract level. Losses from the undercollateralized position are ultimately socialised to the insurance fund or other participants.

---

### Likelihood Explanation

The path is reachable through the normal sequencer flow (`CreateIsolatedSubaccount` → `MatchOrders`). The sequencer's only on-chain health gate is `isHealthy`, which always returns `true`. No admin key, governance action, or sequencer compromise is required; a trader simply crafts an `appendix` with bit 8 set and bits [127:64] zeroed, submits a valid EIP-712 signature, and the sequencer has no contract-level reason to reject either transaction.

---

### Recommendation

1. **Revert on zero margin** inside `createIsolatedSubaccount`:
   ```solidity
   require(margin > 0, "isolated margin must be > 0");
   ```
2. **Override `isHealthy`** in the production contract to call `clearinghouse.getHealth` and enforce `>= 0` for isolated subaccounts after every fill.
3. Alternatively, enforce a minimum margin floor in `_isolatedMargin` so that a zero-bit appendix cannot produce a zero result.

---

### Proof of Concept

```
1. Craft appendix: bit 8 = 1 (isolated), bits [127:64] = 0 (zero margin)
2. Sign Order{sender=parentSubaccount, ..., appendix=crafted} for productId=perpId
3. Submit CreateIsolatedSubaccount tx via endpoint
   → createIsolatedSubaccount executes, margin=0, balance transfer skipped
   → digestToSubaccount[digest] = isolatedSubaccount (zero quote balance)
4. Submit MatchOrders tx with the same signed order as taker
   → taker.order.sender replaced with isolatedSubaccount
   → _updateBalances: perpEngine.updateBalance(perpId, isolatedSubaccount, +amount, -cost)
   → isHealthy(isolatedSubaccount) → true (unconditional)
5. Assert: spotEngine.getBalance(QUOTE_PRODUCT_ID, isolatedSubaccount).amount == 0
           perpEngine.getBalance(perpId, isolatedSubaccount).amount != 0
   → leveraged perp position open with zero collateral ✓
```

### Citations

**File:** core/contracts/OffchainExchange.sol (L354-356)
```text
    function _isIsolated(uint128 appendix) internal pure returns (bool) {
        return ((appendix >> 8) & 1) == 1;
    }
```

**File:** core/contracts/OffchainExchange.sol (L358-360)
```text
    function _isolatedMargin(uint128 appendix) internal pure returns (uint128) {
        return (appendix >> 64) * (10**12);
    }
```

**File:** core/contracts/OffchainExchange.sol (L625-629)
```text
    function isHealthy(
        bytes32 /* subaccount */
    ) internal view virtual returns (bool) {
        return true;
    }
```

**File:** core/contracts/OffchainExchange.sol (L673-678)
```text
        if (digestToSubaccount[ordersInfo.taker.digest] != bytes32(0)) {
            taker.order.sender = digestToSubaccount[ordersInfo.taker.digest];
        }
        if (digestToSubaccount[ordersInfo.maker.digest] != bytes32(0)) {
            maker.order.sender = digestToSubaccount[ordersInfo.maker.digest];
        }
```

**File:** core/contracts/OffchainExchange.sol (L826-827)
```text
        require(isHealthy(taker.order.sender), ERR_INVALID_TAKER);
        require(isHealthy(maker.order.sender), ERR_INVALID_MAKER);
```

**File:** core/contracts/OffchainExchange.sol (L1040-1072)
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
