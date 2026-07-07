### Title
Stale `parentSubaccounts` After Closure Causes Funds to Be Sent to `bytes32(0)` — (`core/contracts/OffchainExchange.sol`)

### Summary

`_tryCloseIsolatedSubaccount` contains no guard against being called on an already-closed isolated subaccount. Because the only early-return check (`if (productId == 0)`) relies on `RiskHelper.getIsolatedProductId`, which is a **pure** function that decodes the product ID from the bytes32 value itself (not from storage), it always returns non-zero for any isolated subaccount identifier regardless of closure state. After the first closure sets `parentSubaccounts[subaccount] = bytes32(0)`, any subsequent call that finds a non-zero balance will transfer those funds to `bytes32(0)` — a permanent sink.

---

### Finding Description

**Root cause 1 — pure guard does not reflect closure state**

`RiskHelper.getIsolatedProductId` is declared `pure` and reads only from the bytes32 argument:

```solidity
function getIsolatedProductId(bytes32 subaccount) internal pure returns (uint32) {
    if (!isIsolatedSubaccount(subaccount)) { return 0; }
    return uint32((uint256(subaccount) >> 32) & 0xFFFF);
}
``` [1](#0-0) 

The isolated subaccount bytes32 encodes the product ID in bits 32–47 and the magic suffix `0x696F73` ("ios") in the lowest 24 bits. These bits never change. So `getIsolatedProductId` returns a non-zero value for the same bytes32 forever — even after the subaccount has been closed in storage.

**Root cause 2 — `parentSubaccounts` is zeroed on first closure but never re-checked**

On first closure:

```solidity
bytes32 parent = parentSubaccounts[subaccount];   // legitimate parent
// ... transfer vQuoteBalance and quoteBalance to parent ...
parentSubaccounts[subaccount] = bytes32(0);        // cleared
``` [2](#0-1) 

On a second call, `parent` is read again from storage and is now `bytes32(0)`. The function then unconditionally transfers any non-zero `vQuoteBalance` or spot `QUOTE_PRODUCT_ID` balance to that zero address:

```solidity
perpEngine.updateBalance(productId, parent, 0, balance.vQuoteBalance);   // parent == bytes32(0)
spotEngine.updateBalance(QUOTE_PRODUCT_ID, parent, quoteBalance);         // parent == bytes32(0)
``` [3](#0-2) 

**Root cause 3 — `digestToSubaccount` is never cleared, enabling re-crediting of closed subaccounts**

`createIsolatedSubaccount` writes `digestToSubaccount[digest] = isolatedSubaccount` and this mapping is never cleared anywhere in the codebase. `matchOrders` redirects fills to the isolated subaccount via this mapping:

```solidity
if (digestToSubaccount[ordersInfo.taker.digest] != bytes32(0)) {
    taker.order.sender = digestToSubaccount[ordersInfo.taker.digest];
}
``` [4](#0-3) 

After the isolated subaccount is closed, any remaining fill for the same order digest still credits the closed subaccount with a new perp position and `vQuoteBalance`. `matchOrders` itself never calls `_tryCloseIsolatedSubaccount`, so the balance sits in the closed subaccount until the next external trigger.

**Trigger for second closure**

`Clearinghouse.delistProduct` iterates over a sequencer-supplied list of subaccounts, zeroes each perp position, and calls `tryCloseIsolatedSubaccount`:

```solidity
if (RiskHelper.isIsolatedSubaccount(txn.subaccounts[i])) {
    IOffchainExchange(...).tryCloseIsolatedSubaccount(txn.subaccounts[i]);
}
``` [5](#0-4) 

If the closed isolated subaccount received a fill after its first closure and is included in the `delistProduct` subaccount list, the second `tryCloseIsolatedSubaccount` call transfers the re-credited `vQuoteBalance` (and any spot quote balance) to `bytes32(0)`.

`ClearinghouseLiq._finalizeSubaccount` is a second trigger via the liquidation path: [6](#0-5) 

---

### Impact Explanation

Any `vQuoteBalance` (realized perp PnL) or `QUOTE_PRODUCT_ID` spot balance that accumulates in a closed isolated subaccount — via a post-closure `matchOrders` fill — is permanently transferred to the `bytes32(0)` subaccount instead of the legitimate parent. The parent loses those funds with no recovery path. This constitutes direct asset theft / permanent loss of user funds, matching the Critical scope.

---

### Likelihood Explanation

The sequence requires: (1) an isolated subaccount is closed via liquidation or finalization; (2) a subsequent `matchOrders` fill is processed for the same order digest (possible because `digestToSubaccount` is never cleared and the order may be partially filled); (3) a second closure trigger fires (`delistProduct` or another liquidation pass). Steps 1 and 3 are normal protocol operations. Step 2 is a natural race condition in any active market. No attacker-controlled privileges are required beyond submitting a valid signed order.

---

### Recommendation

Add a guard at the top of `_tryCloseIsolatedSubaccount` that returns early if the subaccount has already been closed:

```solidity
function _tryCloseIsolatedSubaccount(bytes32 subaccount) internal {
    uint32 productId = RiskHelper.getIsolatedProductId(subaccount);
    if (productId == 0) { return; }
+   if (parentSubaccounts[subaccount] == bytes32(0)) { return; }  // already closed
    ...
}
```

Additionally, clear `digestToSubaccount[digest]` when an isolated subaccount is closed to prevent post-closure fills from re-crediting it.

---

### Proof of Concept

```
1. Alice creates an isolated subaccount S for product P via createIsolatedSubaccount.
   - parentSubaccounts[S] = Alice_subaccount
   - digestToSubaccount[digest] = S

2. S is liquidated via ClearinghouseLiq._finalizeSubaccount:
   - perpEngine balance zeroed, vQuoteBalance transferred to Alice_subaccount
   - spotEngine QUOTE balance transferred to Alice_subaccount
   - parentSubaccounts[S] = bytes32(0)   ← CLOSED

3. Sequencer processes a matchOrders fill for the same digest:
   - digestToSubaccount[digest] == S (never cleared)
   - taker.order.sender is replaced with S
   - perpEngine.updateBalance credits S with amountDelta and vQuoteBalance
   - S now has: balance.amount != 0 (open position), vQuoteBalance != 0

4. Product P is delisted via Clearinghouse.delistProduct with S in txn.subaccounts:
   - perpEngine.updateBalance(P, S, -balance.amount, quoteDelta)
     → balance.amount = 0, vQuoteBalance updated
   - tryCloseIsolatedSubaccount(S) called:
     - getIsolatedProductId(S) → non-zero (pure, reads bytes32)
     - balance.amount == 0 → enters closure block
     - parent = parentSubaccounts[S] = bytes32(0)
     - perpEngine.updateBalance(P, bytes32(0), 0, vQuoteBalance)  ← FUNDS LOST
     - spotEngine.updateBalance(QUOTE_PRODUCT_ID, bytes32(0), quoteBalance) ← FUNDS LOST

5. Assert: spotEngine.getBalance(QUOTE_PRODUCT_ID, bytes32(0)).amount increased.
   Alice's vQuoteBalance and quote balance are permanently lost.
```

### Citations

**File:** core/contracts/libraries/RiskHelper.sol (L91-100)
```text
    function getIsolatedProductId(bytes32 subaccount)
        internal
        pure
        returns (uint32)
    {
        if (!isIsolatedSubaccount(subaccount)) {
            return 0;
        }
        return uint32((uint256(subaccount) >> 32) & 0xFFFF);
    }
```

**File:** core/contracts/OffchainExchange.sol (L172-204)
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
            }
            isolatedSubaccountsMask[addr] &= ~uint256(0) ^ (1 << id);
            isolatedSubaccounts[parent][id] = bytes32(0);
            parentSubaccounts[subaccount] = bytes32(0);
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

**File:** core/contracts/Clearinghouse.sol (L319-323)
```text
            if (RiskHelper.isIsolatedSubaccount(txn.subaccounts[i])) {
                IOffchainExchange(
                    IEndpoint(getEndpoint()).getOffchainExchange()
                ).tryCloseIsolatedSubaccount(txn.subaccounts[i]);
            }
```

**File:** core/contracts/ClearinghouseLiq.sol (L621-625)
```text
            if (RiskHelper.isIsolatedSubaccount(txn.liquidatee)) {
                IOffchainExchange(
                    IEndpoint(getEndpoint()).getOffchainExchange()
                ).tryCloseIsolatedSubaccount(txn.liquidatee);
            }
```
