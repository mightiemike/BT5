### Title
Repeated `createIsolatedSubaccount` Calls with Distinct Nonces Drain Parent Quote Balance Without Bound — (`core/contracts/OffchainExchange.sol`)

---

### Summary

`createIsolatedSubaccount` guards against replaying the **same digest** but not against submitting N distinct orders (different nonces) that all resolve to the **same existing isolated subaccount** for a given `productId`. Each call unconditionally transfers `margin` from the parent to the isolated subaccount, with no health check on the parent afterward. An attacker can drain the parent's `QUOTE_PRODUCT_ID` balance to an arbitrarily negative value.

---

### Finding Description

The replay guard at the top of `createIsolatedSubaccount` is keyed on the EIP-712 digest: [1](#0-0) 

Because the digest includes the `nonce` field of the order, two orders with different nonces produce two different digests. The guard therefore does **not** fire on the second call.

After the guard, the function searches for an existing isolated subaccount for the same `productId`: [2](#0-1) 

When one is found, `newIsolatedSubaccount` is set to the existing subaccount and the creation block is skipped entirely. Execution then falls through unconditionally to the margin transfer: [3](#0-2) 

The margin transfer is **not** conditioned on whether the subaccount was newly created. It fires for every new digest, even when reusing an existing isolated subaccount. There is no `getHealth` call on `txn.order.sender` anywhere in this function or in the `EndpointTx.sol` dispatch path that invokes it: [4](#0-3) 

---

### Impact Explanation

For N calls with N distinct nonces, all for the same `productId` and margin `M`:

- `parent.quoteBalance` decreases by `M` on each call → net change: `-N*M`
- `isolatedSubaccount.quoteBalance` increases by `M` on each call → net change: `+N*M`
- The parent's balance can be driven arbitrarily negative (bad debt), while the isolated subaccount accumulates `N*M` collateral that can be used to open and profit from positions, or simply extracted when the isolated subaccount is closed and its balance is returned to the parent — but by then the parent's debt has already been socialized.

This matches the Critical impact category: **bad debt creation** and **asset theft from the parent subaccount**.

---

### Likelihood Explanation

The attack path is fully permissionless and externally reachable:

1. Attacker controls a normal (non-isolated) subaccount with balance `M`.
2. Attacker submits `CreateIsolatedSubaccount` transactions via the sequencer's `submitTransactions` path, each with a fresh nonce and the same `productId` and `margin=M`.
3. The sequencer is expected to process these; there is no on-chain gate preventing multiple such transactions for the same `productId`.
4. No privileged role, leaked key, or governance action is required.

---

### Recommendation

Move the margin transfer inside the new-subaccount creation block (`if (newIsolatedSubaccount == bytes32(0))`), so margin is only transferred once — when the isolated subaccount is first created. Subsequent orders for the same `productId` should reuse the existing subaccount without transferring additional margin.

Alternatively, add a check before the margin transfer that verifies no margin has already been committed to the target isolated subaccount for any prior digest (e.g., by checking `digestToMargin` for all existing digests mapped to `newIsolatedSubaccount`, or by tracking a per-subaccount "pending margin" counter).

Additionally, add a `getHealth(txn.order.sender, HealthType.INITIAL) >= 0` assertion after the balance update, consistent with how other collateral-moving operations (e.g., `withdrawCollateral`, `mintNlp`) protect the parent. [5](#0-4) 

---

### Proof of Concept

```
State: parent subaccount P has quoteBalance = M (exactly one unit of margin).

Tx1: CreateIsolatedSubaccount(order{nonce=1, productId=X, margin=M})
  → digest1 is new → no early return
  → no existing iso subaccount for X → creates iso1
  → digestToSubaccount[digest1] = iso1
  → parent.quoteBalance -= M  →  0
  → iso1.quoteBalance  += M  →  M

Tx2: CreateIsolatedSubaccount(order{nonce=2, productId=X, margin=M})
  → digest2 is new → no early return
  → iso1 already exists for productId X → newIsolatedSubaccount = iso1
  → digestToSubaccount[digest2] = iso1
  → parent.quoteBalance -= M  →  -M   ← BAD DEBT
  → iso1.quoteBalance  += M  →  2M

TxN: same pattern → parent.quoteBalance = -(N-1)*M, iso1.quoteBalance = N*M
```

Assert: after K transactions, `parent.quoteBalance == M - K*M` and `iso1.quoteBalance == K*M`. The parent is insolvent after the first repeat call.

### Citations

**File:** core/contracts/OffchainExchange.sol (L1008-1011)
```text
        bytes32 digest = getDigest(txn.productId, txn.order);
        if (digestToSubaccount[digest] != bytes32(0)) {
            return digestToSubaccount[digest];
        }
```

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

**File:** core/contracts/EndpointTx.sol (L620-631)
```text
            txType == IEndpoint.TransactionType.CreateIsolatedSubaccount
        ) {
            IEndpoint.CreateIsolatedSubaccount memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.CreateIsolatedSubaccount)
            );
            bytes32 newIsolatedSubaccount = IOffchainExchange(offchainExchange)
                .createIsolatedSubaccount(
                    txn,
                    getLinkedSigner(txn.order.sender)
                );
            _recordSubaccount(newIsolatedSubaccount);
```

**File:** core/contracts/Clearinghouse.sol (L415-419)
```text
        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
```
