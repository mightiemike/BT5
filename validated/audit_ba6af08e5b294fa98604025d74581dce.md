### Title
Stale `digestToSubaccount` After Isolated Subaccount Closure Enables Undercollateralized Position Creation — (`File: core/contracts/OffchainExchange.sol`)

---

### Summary

`_tryCloseIsolatedSubaccount` clears the parent-linkage mappings for a closed isolated subaccount but never clears `digestToSubaccount[digest]`. After closure, `matchOrders` still reads the stale mapping and routes subsequent fills of the same order to the now-orphaned isolated subaccount. Because the subaccount has no margin and `parentSubaccounts` is zeroed out, any resulting position is undercollateralized and, when eventually closed, credits residual funds to `bytes32(0)` — permanently destroying them.

---

### Finding Description

When an isolated subaccount is closed, `_tryCloseIsolatedSubaccount` performs the following cleanup: [1](#0-0) 

```solidity
isolatedSubaccountsMask[addr] &= ~uint256(0) ^ (1 << id);
isolatedSubaccounts[parent][id] = bytes32(0);
parentSubaccounts[subaccount] = bytes32(0);
```

It does **not** clear `digestToSubaccount[digest]` or `digestToMargin[digest]`. [2](#0-1) 

In `matchOrders`, the sender override is applied unconditionally whenever `digestToSubaccount[digest] != bytes32(0)`: [3](#0-2) 

```solidity
if (digestToSubaccount[ordersInfo.taker.digest] != bytes32(0)) {
    taker.order.sender = digestToSubaccount[ordersInfo.taker.digest];
}
```

There is no check that the mapped isolated subaccount is still active. After closure, `parentSubaccounts[subaccount]` is `bytes32(0)`: [4](#0-3) 

So when `_tryCloseIsolatedSubaccount` is triggered a second time on the re-opened subaccount, `parent` resolves to `bytes32(0)` and all residual balances are credited there:

```solidity
bytes32 parent = parentSubaccounts[subaccount]; // == bytes32(0)
...
perpEngine.updateBalance(productId, parent, 0, balance.vQuoteBalance);  // → zero address
spotEngine.updateBalance(QUOTE_PRODUCT_ID, parent, quoteBalance);        // → zero address
```

The post-trade health check cannot catch this because `isHealthy` is a stub that always returns `true`: [5](#0-4) 

```solidity
function isHealthy(bytes32 /* subaccount */) internal view virtual returns (bool) {
    return true;
}
```

---

### Impact Explanation

A closed isolated subaccount can receive a new perp position with zero quote-margin backing. The position is accepted because `isHealthy` always returns `true`. When that position is subsequently closed (reduce-only fill, liquidation, or delisting), `_tryCloseIsolatedSubaccount` credits all remaining `vQuoteBalance` and spot quote balance to `bytes32(0)`, permanently destroying the user's funds. The counterparty trading against the undercollateralized subaccount also bears unhedged credit risk.

**Corrupted state delta**: `spotEngine` and `perpEngine` balances for `bytes32(0)` are inflated; the user's real subaccount never receives the funds.

---

### Likelihood Explanation

The scenario is realistic in volatile markets:

1. User signs an isolated perp order for N units.
2. A partial fill creates the isolated subaccount; `digestToSubaccount[digest]` is set.
3. The position is liquidated (or the product is delisted via `delistProduct`). `_tryCloseIsolatedSubaccount` is called, zeroing `parentSubaccounts` but leaving `digestToSubaccount[digest]` intact.
4. The original order still has remaining fill amount (`filledAmounts[digest] < order.amount`) and has not expired.
5. The sequencer, following normal order-matching logic, submits another `matchOrders` for the remaining amount. The stale mapping routes the fill to the orphaned isolated subaccount.

Steps 1–4 require only normal user and market activity. Step 5 is the sequencer executing a structurally valid transaction; no malicious sequencer behavior is required.

---

### Recommendation

In `_tryCloseIsolatedSubaccount`, after clearing the parent-linkage mappings, also clear the order-digest mappings. The digest must be stored on the isolated subaccount (or passed in) so it can be looked up at close time. Alternatively, maintain a reverse mapping `subaccountToDigest` and clear both directions:

```solidity
bytes32 digest = subaccountToDigest[subaccount];
if (digest != bytes32(0)) {
    digestToSubaccount[digest] = bytes32(0);
    digestToMargin[digest] = 0;
    subaccountToDigest[subaccount] = bytes32(0);
}
```

Additionally, `isHealthy` should be implemented to perform a real health check on isolated subaccounts rather than unconditionally returning `true`.

---

### Proof of Concept

1. Alice signs an isolated order: `amount = 100`, `nonce = 1`, producing `digest = D`.
2. Sequencer calls `matchOrders` for 60 units. `createIsolatedSubaccount` sets `digestToSubaccount[D] = isoAcct`. `filledAmounts[D] = 60`.
3. `isoAcct` is liquidated. `_tryCloseIsolatedSubaccount` is called:
   - `parentSubaccounts[isoAcct] = bytes32(0)` ✓
   - `digestToSubaccount[D]` is **not** cleared ✗
4. Sequencer calls `matchOrders` for the remaining 40 units using the same signed order.
   - `digestToSubaccount[D] != bytes32(0)` → `taker.order.sender = isoAcct`
   - `isHealthy(isoAcct)` returns `true` (stub)
   - Fill executes; `isoAcct` now holds a 40-unit perp position with zero quote margin and `parentSubaccounts[isoAcct] = bytes32(0)`.
5. Position is closed. `_tryCloseIsolatedSubaccount` runs again:
   - `parent = parentSubaccounts[isoAcct] = bytes32(0)`
   - Any `vQuoteBalance` or spot quote balance is credited to `bytes32(0)` — funds are permanently lost. [6](#0-5) [3](#0-2) [7](#0-6)

### Citations

**File:** core/contracts/OffchainExchange.sol (L56-59)
```text
    mapping(bytes32 => bytes32) internal digestToSubaccount;

    // how much margin does an isolated order require
    mapping(bytes32 => int128) internal digestToMargin;
```

**File:** core/contracts/OffchainExchange.sol (L160-208)
```text
    function _tryCloseIsolatedSubaccount(bytes32 subaccount) internal {
        uint32 productId = RiskHelper.getIsolatedProductId(subaccount);
        if (productId == 0) {
            return;
        }
        IPerpEngine.Balance memory balance = perpEngine.getBalance(
            productId,
            subaccount
        );
        if (balance.amount == 0) {
            uint8 id = RiskHelper.getIsolatedId(subaccount);
            address addr = address(uint160(bytes20(subaccount)));
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
            }
            isolatedSubaccountsMask[addr] &= ~uint256(0) ^ (1 << id);
            isolatedSubaccounts[parent][id] = bytes32(0);
            parentSubaccounts[subaccount] = bytes32(0);

            emit CloseIsolatedSubaccount(subaccount, parent);
        }
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
