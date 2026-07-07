Now I have enough information to analyze the full exploit path. Let me verify the key facts:

1. `_tryCloseIsolatedSubaccount` clears `parentSubaccounts`, `isolatedSubaccounts`, and `isolatedSubaccountsMask` but **never** clears `digestToSubaccount[digest]`
2. `matchOrders` unconditionally replaces `taker.order.sender` with `digestToSubaccount[digest]` if non-zero
3. `_validateOrder` has no check that the resolved isolated subaccount is still active/open
4. `isHealthy` in the base `OffchainExchange` always returns `true`

---

### Title
Stale `digestToSubaccount` Entry After Isolated Subaccount Closure Allows Re-Fill Into Orphaned Subaccount, Permanently Locking Funds — (`core/contracts/OffchainExchange.sol`)

### Summary

`_tryCloseIsolatedSubaccount` clears the parent/child linkage mappings but never clears `digestToSubaccount[digest]`. A subsequent `matchOrders` call using the same partially-filled order digest resolves the sender to the now-closed isolated subaccount, credits it with base/quote balances, and leaves those funds permanently unreachable because `parentSubaccounts[iso] == bytes32(0)`.

### Finding Description

**Storage state after `createIsolatedSubaccount`:** [1](#0-0) 

`digestToSubaccount[digest] = iso`, `parentSubaccounts[iso] = parent`, `isolatedSubaccounts[parent][id] = iso`.

**`_tryCloseIsolatedSubaccount` clears linkage but not `digestToSubaccount`:** [2](#0-1) 

Lines 202–204 zero out `isolatedSubaccountsMask`, `isolatedSubaccounts[parent][id]`, and `parentSubaccounts[iso]`. `digestToSubaccount[digest]` is never touched anywhere in this function.

**`matchOrders` blindly substitutes the stale entry:** [3](#0-2) 

If `digestToSubaccount[digest] != bytes32(0)`, `taker.order.sender` is overwritten with the closed isolated subaccount — no liveness check.

**`_validateOrder` has no isolated-subaccount-active check:** [4](#0-3) 

It only checks: version, maker/taker-only flags, remaining fill amount, signature, and expiration. There is no check that `parentSubaccounts[order.sender] != bytes32(0)`. The signature check passes because `address(uint160(bytes20(iso))) == address(uint160(bytes20(parent)))` — the embedded address is identical.

**`_updateBalances` credits the orphaned subaccount:** [5](#0-4) 

**`isHealthy` does not block it:** [6](#0-5) 

Always returns `true` in the base contract.

### Impact Explanation

After the re-fill, the closed isolated subaccount accumulates a non-zero spot or perp balance. Because `parentSubaccounts[iso] == bytes32(0)`, no parent can claim the funds via `_tryCloseIsolatedSubaccount`. If `_tryCloseIsolatedSubaccount` is called again on the same `iso`, it would attempt to transfer balances to `bytes32(0)` (the zero-address subaccount), effectively burning them. Either way, the user's funds are permanently lost.

### Likelihood Explanation

The precondition — partial fill followed by position closure to zero (via a reduce-only order or liquidation) followed by a `CloseIsolatedSubaccount` transaction — is a normal, supported protocol flow. The sequencer then re-submits the remaining fill of the same order (also normal behavior for partially-filled orders). No attacker privilege is required; the sequencer executes this path in the ordinary course of operations.

### Recommendation

In `_tryCloseIsolatedSubaccount`, iterate over all digests associated with the isolated subaccount and clear them, **or** add a guard in `matchOrders` / `_validateOrder` that rejects any order whose resolved `digestToSubaccount` entry points to a subaccount with `parentSubaccounts[iso] == bytes32(0)`:

```solidity
// In matchOrders, after digestToSubaccount substitution:
if (digestToSubaccount[ordersInfo.taker.digest] != bytes32(0)) {
    bytes32 iso = digestToSubaccount[ordersInfo.taker.digest];
    require(parentSubaccounts[iso] != bytes32(0), ERR_INVALID_TAKER);
    taker.order.sender = iso;
}
```

Alternatively, clear `digestToSubaccount[digest]` inside `_tryCloseIsolatedSubaccount` by tracking which digests map to each isolated subaccount (a reverse mapping).

### Proof of Concept

```
1. Alice signs an isolated perp order (digest D) for productId P, amount = 100.
2. createIsolatedSubaccount(D) → iso created; digestToSubaccount[D] = iso.
3. matchOrders partial fill: filledAmounts[D] = 60; perpEngine.balance[P][iso].amount = 60.
4. Alice submits a reduce-only order that closes the position: perpEngine.balance[P][iso].amount = 0.
5. Sequencer submits CloseIsolatedSubaccount(iso):
   - _tryCloseIsolatedSubaccount: balance.amount == 0 → proceeds.
   - parentSubaccounts[iso] = bytes32(0); isolatedSubaccounts[parent][id] = bytes32(0).
   - digestToSubaccount[D] still = iso.  ← BUG
6. Sequencer submits matchOrders with the same order (digest D), remaining amount = 40:
   - digestToSubaccount[D] = iso → taker.order.sender = iso.
   - _validateOrder: filledAmounts[D]=60, order.amount=100-60=40 ≠ 0, signature valid → passes.
   - _updateBalances: perpEngine.balance[P][iso].amount += 40.
   - isHealthy(iso) → true.
7. Assert: perpEngine.balance[P][iso].amount = 40, parentSubaccounts[iso] = bytes32(0).
   Funds are permanently locked in iso with no reachable parent.
```

### Citations

**File:** core/contracts/OffchainExchange.sol (L202-204)
```text
            isolatedSubaccountsMask[addr] &= ~uint256(0) ^ (1 << id);
            isolatedSubaccounts[parent][id] = bytes32(0);
            parentSubaccounts[subaccount] = bytes32(0);
```

**File:** core/contracts/OffchainExchange.sol (L435-468)
```text
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

**File:** core/contracts/OffchainExchange.sol (L811-817)
```text
        _updateBalances(
            callState,
            market.quoteId,
            taker.order.sender,
            ordersInfo.taker.amountDelta,
            ordersInfo.taker.quoteDelta
        );
```

**File:** core/contracts/OffchainExchange.sol (L1064-1072)
```text
            parentSubaccounts[newIsolatedSubaccount] = txn.order.sender;
            isolatedSubaccounts[txn.order.sender][id] = newIsolatedSubaccount;
            _onCreateIsolatedSubaccount(
                newIsolatedSubaccount,
                txn.order.sender
            );
        }

        digestToSubaccount[digest] = newIsolatedSubaccount;
```
