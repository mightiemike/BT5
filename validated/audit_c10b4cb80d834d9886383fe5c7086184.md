### Title
Missing Parent Subaccount Health Check After Margin Transfer in `createIsolatedSubaccount()` — (`File: core/contracts/OffchainExchange.sol`)

---

### Summary

In `createIsolatedSubaccount()`, when margin (quote balance) is transferred from the parent subaccount to the newly created isolated subaccount, there is no validation that the parent subaccount remains above initial health. Every other balance-reducing operation in the protocol enforces a post-transfer health check on the sender, but `createIsolatedSubaccount` does not. This allows a user to drain their parent subaccount's quote balance into an isolated subaccount, leaving the parent undercollateralized while the transferred funds are shielded from the parent's liquidation.

---

### Finding Description

In `OffchainExchange.sol`, `createIsolatedSubaccount()` extracts a `margin` value from the order's `appendix` field and transfers it from the parent subaccount to the new isolated subaccount: [1](#0-0) 

The `margin` value is fully user-controlled via the `appendix` field of the signed order: [2](#0-1) 

No health check is performed on `txn.order.sender` (the parent) after the quote balance is reduced. Compare this to every other balance-reducing path in the protocol:

- `withdrawCollateral` in `Clearinghouse.sol` explicitly requires `getHealth(sender, healthType) >= 0` after the debit: [3](#0-2) 

- `transferQuote` in `Clearinghouse.sol` requires `_isAboveInitial(txn.sender)` after the debit: [4](#0-3) 

`createIsolatedSubaccount` has no equivalent guard. The `EndpointTx.sol` dispatch path for `CreateIsolatedSubaccount` also adds no health check: [5](#0-4) 

The isolated subaccount is a separate accounting context. Its balances are not included in the parent's health calculation. Once margin is moved there, it is shielded from the parent's liquidation — the parent's health degrades while the transferred funds remain safe in the isolated subaccount.

---

### Impact Explanation

A user with leveraged positions in their parent subaccount can sign a `CreateIsolatedSubaccount` transaction with `margin` set to their entire quote balance. When the sequencer executes it, the parent's quote balance drops to zero (or below the required collateral threshold), making the parent undercollateralized. The parent becomes liquidatable, but the margin transferred to the isolated subaccount is not reachable by the liquidator acting on the parent. The user effectively escapes the collateral consequences of their leveraged parent position by siphoning quote into an isolated context — directly analogous to the Beedle borrower pushing a high-LTV loan to a pool that did not consent to it.

The corrupted state delta is: `parent.quoteBalance -= margin` with no on-chain enforcement that `getHealth(parent, INITIAL) >= 0` holds afterward.

---

### Likelihood Explanation

The `CreateIsolatedSubaccount` transaction type is processed by the sequencer via `processTransactionImpl`. The user signs the transaction including the `margin` value encoded in `appendix`. The sequencer is expected to execute signed user transactions faithfully. Because the on-chain contract imposes no health constraint after the margin transfer, there is no on-chain backstop. A user who understands the encoding of `appendix` can craft a valid signed order with an arbitrarily large `margin` value. The sequencer has no contract-enforced reason to reject it.

---

### Recommendation

Add a parent health check after the margin transfer in `createIsolatedSubaccount`, consistent with `withdrawCollateral` and `transferQuote`:

```solidity
// After the margin transfer block (line ~1087)
if (margin > 0) {
    require(
        IClearinghouse(clearinghouse).getHealth(
            txn.order.sender,
            IProductEngine.HealthType.INITIAL
        ) >= 0,
        ERR_SUBACCT_HEALTH
    );
}
```

---

### Proof of Concept

1. Parent subaccount holds 100 USDC quote balance and an open leveraged perp position requiring 90 USDC as initial margin.
2. User signs a `CreateIsolatedSubaccount` order for any perp product with `margin = 100e18` encoded in `appendix` bits `[127:64]`.
3. Sequencer executes `processTransactionImpl` → `createIsolatedSubaccount`.
4. `spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.order.sender, -100e18)` executes at line 1077–1081; parent quote balance becomes 0.
5. `spotEngine.updateBalance(QUOTE_PRODUCT_ID, newIsolatedSubaccount, 100e18)` executes at line 1082–1086.
6. No health check fires. Parent's initial health is now negative (perp position unsupported by collateral).
7. Parent is liquidatable. Liquidator seizes the perp position at a penalty price.
8. The 100 USDC in the isolated subaccount is unreachable by the liquidator acting on the parent — user retains it.

The missing guard is at: [1](#0-0)

### Citations

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

**File:** core/contracts/OffchainExchange.sol (L1358-1360)
```text

```

**File:** core/contracts/Clearinghouse.sol (L247-249)
```text
        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -toTransfer);
        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.recipient, toTransfer);
        require(_isAboveInitial(txn.sender), ERR_SUBACCT_HEALTH);
```

**File:** core/contracts/Clearinghouse.sol (L415-419)
```text
        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
```

**File:** core/contracts/EndpointTx.sol (L619-632)
```text
        } else if (
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
        } else if (
```
