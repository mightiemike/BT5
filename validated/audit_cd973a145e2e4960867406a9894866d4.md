### Title
`createIsolatedSubaccount` Transfers Margin From Parent Without Checking If Parent Is Liquidatable — (`core/contracts/OffchainExchange.sol`)

---

### Summary

`OffchainExchange.createIsolatedSubaccount` deducts margin from a parent subaccount and credits a new isolated subaccount without verifying that the parent subaccount is above initial (or maintenance) health after the transfer. A parent subaccount that is already under maintenance health — and therefore eligible for liquidation — can still create isolated subaccounts and move collateral out, worsening its own health and reducing the collateral available to liquidators.

---

### Finding Description

When `createIsolatedSubaccount` is called with `margin > 0` encoded in the order appendix, it directly calls `spotEngine.updateBalance` twice: once to deduct margin from the parent and once to credit the new isolated subaccount.

```solidity
// OffchainExchange.sol lines 1074–1087
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
``` [1](#0-0) 

No health check is performed on `txn.order.sender` after this deduction. Compare this to `Clearinghouse.transferQuote`, which enforces an explicit post-transfer health check:

```solidity
// Clearinghouse.sol line 249
require(_isAboveInitial(txn.sender), ERR_SUBACCT_HEALTH);
``` [2](#0-1) 

The `transferQuote` path is the intended mechanism for moving quote between subaccounts, and it correctly gates the transfer on the sender remaining above initial health. The `createIsolatedSubaccount` path bypasses this gate entirely.

The `processTransactionImpl` handler for `CreateIsolatedSubaccount` also performs no health check before or after the call:

```solidity
// EndpointTx.sol lines 620–631
} else if (txType == IEndpoint.TransactionType.CreateIsolatedSubaccount) {
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
}
``` [3](#0-2) 

The liquidation entry point in `ClearinghouseLiq.liquidateSubaccountImpl` correctly requires `isUnderMaintenance(txn.liquidatee)` before proceeding, confirming that maintenance health is the protocol's intended liquidation threshold: [4](#0-3) 

However, nothing prevents a subaccount that is already below that threshold from calling `createIsolatedSubaccount` and moving margin out before a liquidator acts.

---

### Impact Explanation

A parent subaccount that is already under maintenance health can:

1. Sign a `CreateIsolatedSubaccount` order with a non-zero `isolatedMargin` value in the appendix.
2. Have the sequencer include the transaction (the protocol imposes no on-chain guard).
3. Move quote collateral from the parent to the isolated subaccount.
4. The isolated subaccount is a separate accounting entity; its balance is not directly accessible to liquidators targeting the parent.
5. The parent's health decreases further, increasing the likelihood that the insurance fund must absorb bad debt during finalization.

The concrete corrupted state: the parent subaccount's `QUOTE_PRODUCT_ID` balance in `SpotEngine` is reduced by `margin` without any health invariant being enforced, while the same amount is locked in an isolated subaccount that is outside the parent's liquidation scope.

---

### Likelihood Explanation

The `CreateIsolatedSubaccount` transaction type is processed through the sequencer path (`processTransactionImpl`). The sequencer is a trusted but not infallible entity; the protocol's design intent is that on-chain contracts enforce all safety invariants independently of sequencer behavior. The user controls the signed order (including the margin amount) and the sequencer has no on-chain obligation to check the parent's health before including the transaction. The signed order is valid as long as the signature is correct and the order is not expired — both of which are checked — but health is not.

---

### Recommendation

Add a post-transfer health check in `createIsolatedSubaccount` after the margin deduction, mirroring the pattern used in `Clearinghouse.transferQuote`:

```solidity
if (margin > 0) {
    digestToMargin[digest] = margin;
    spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.order.sender, -margin);
    spotEngine.updateBalance(QUOTE_PRODUCT_ID, newIsolatedSubaccount, margin);
    // Add: ensure parent remains above initial health after margin transfer
    require(
        clearinghouse.getHealth(txn.order.sender, IProductEngine.HealthType.INITIAL) >= 0,
        ERR_SUBACCT_HEALTH
    );
}
```

---

### Proof of Concept

1. Parent subaccount `P` has maintenance health just below zero (e.g., health = −1e18), making it eligible for liquidation.
2. Before any liquidator acts, the user signs a `CreateIsolatedSubaccount` order for product `X` with `isolatedMargin = M` encoded in the appendix.
3. The sequencer includes the `CreateIsolatedSubaccount` transaction in a batch submitted via `submitTransactionsChecked`.
4. `processTransactionImpl` dispatches to `OffchainExchange.createIsolatedSubaccount`.
5. The function verifies the signature (passes), creates isolated subaccount `I`, then executes:
   - `spotEngine.updateBalance(QUOTE_PRODUCT_ID, P, -M)` — parent loses `M` quote.
   - `spotEngine.updateBalance(QUOTE_PRODUCT_ID, I, +M)` — isolated subaccount gains `M` quote.
6. No health check is performed. Parent's health is now `−1e18 − M * weight`, deeper underwater.
7. When a liquidator subsequently calls `liquidateSubaccountImpl` on `P`, the parent has less quote collateral available. The insurance fund must cover a larger shortfall during finalization via `_finalizeSubaccount`. [5](#0-4) [6](#0-5)

### Citations

**File:** core/contracts/OffchainExchange.sol (L999-1090)
```text
    function createIsolatedSubaccount(
        IEndpoint.CreateIsolatedSubaccount memory txn,
        address linkedSigner
    ) external onlyEndpoint returns (bytes32) {
        require(
            !RiskHelper.isIsolatedSubaccount(txn.order.sender),
            ERR_UNAUTHORIZED
        );
        require(_isIsolated(txn.order.appendix), ERR_UNAUTHORIZED);
        bytes32 digest = getDigest(txn.productId, txn.order);
        if (digestToSubaccount[digest] != bytes32(0)) {
            return digestToSubaccount[digest];
        }
        require(
            _checkSignature(
                txn.order.sender,
                digest,
                linkedSigner,
                txn.signature
            ),
            ERR_INVALID_SIGNATURE
        );

        address senderAddress = address(uint160(bytes20(txn.order.sender)));
        uint256 mask = isolatedSubaccountsMask[senderAddress];
        bytes32 newIsolatedSubaccount = bytes32(0);
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

        return newIsolatedSubaccount;
    }
```

**File:** core/contracts/Clearinghouse.sol (L247-249)
```text
        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -toTransfer);
        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.recipient, toTransfer);
        require(_isAboveInitial(txn.sender), ERR_SUBACCT_HEALTH);
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

**File:** core/contracts/ClearinghouseLiq.sol (L279-413)
```text
    function _finalizeSubaccount(
        IEndpoint.LiquidateSubaccount calldata txn,
        ISpotEngine spotEngine,
        IPerpEngine perpEngine
    ) internal returns (bool) {
        if (txn.productId != type(uint32).max) {
            return false;
        }
        // check whether the subaccount can be finalized:
        // - all perps positions have closed
        // - all spread positions have closed
        // - all spot assets have closed
        // - all positive pnls have been settled

        FinalizeVars memory v;

        v.spotIds = spotEngine.getProductIds();
        v.perpIds = perpEngine.getProductIds();

        require(v.spotIds[0] == QUOTE_PRODUCT_ID);

        // all spot assets (except USDC) must be closed out
        for (uint32 i = 1; i < v.spotIds.length; ++i) {
            uint32 spotId = v.spotIds[i];
            if (spotEngine.getRisk(spotId).longWeightInitialX18 == 0) {
                continue;
            }
            ISpotEngine.Balance memory balance = spotEngine.getBalance(
                spotId,
                txn.liquidatee
            );
            require(balance.amount <= 0, ERR_NOT_FINALIZABLE_SUBACCOUNT);
        }

        for (uint32 i = 0; i < v.perpIds.length; ++i) {
            uint32 perpId = v.perpIds[i];
            IPerpEngine.Balance memory balance = perpEngine.getBalance(
                perpId,
                txn.liquidatee
            );
            require(balance.amount == 0, ERR_NOT_FINALIZABLE_SUBACCOUNT);
        }

        // settle all positive pnl
        for (uint32 i = 0; i < v.perpIds.length; ++i) {
            uint32 perpId = v.perpIds[i];
            IPerpEngine.Balance memory balance = perpEngine.getBalance(
                perpId,
                txn.liquidatee
            );
            if (balance.vQuoteBalance > 0) {
                _settlePnlAgainstLiquidator(
                    txn,
                    perpId,
                    balance.vQuoteBalance,
                    spotEngine,
                    perpEngine
                );
            }
        }

        ISpotEngine.Balance memory quoteBalance = spotEngine.getBalance(
            QUOTE_PRODUCT_ID,
            txn.liquidatee
        );

        // settle all negative pnl until quote balance becomes 0
        for (uint32 i = 0; i < v.perpIds.length; ++i) {
            uint32 perpId = v.perpIds[i];
            IPerpEngine.Balance memory balance = perpEngine.getBalance(
                perpId,
                txn.liquidatee
            );
            if (balance.vQuoteBalance < 0 && quoteBalance.amount > 0) {
                int128 canSettle = MathHelper.max(
                    balance.vQuoteBalance,
                    -quoteBalance.amount
                );
                _settlePnlAgainstLiquidator(
                    txn,
                    perpId,
                    canSettle,
                    spotEngine,
                    perpEngine
                );
                quoteBalance.amount += canSettle;
            }
        }

        v.insurance = insurance;
        v.insurance -= lastLiquidationFees;
        v.canLiquidateMore = (quoteBalance.amount + v.insurance) > 0;

        if (v.canLiquidateMore) {
            for (uint32 i = 1; i < v.spotIds.length; ++i) {
                uint32 spotId = v.spotIds[i];
                ISpotEngine.Balance memory balance = spotEngine.getBalance(
                    spotId,
                    txn.liquidatee
                );
                if (spotEngine.getRisk(spotId).longWeightInitialX18 == 0) {
                    continue;
                }
                require(balance.amount == 0, ERR_NOT_FINALIZABLE_SUBACCOUNT);
            }
        }

        v.insurance = perpEngine.socializeSubaccount(
            txn.liquidatee,
            v.insurance
        );

        // we can assure that quoteBalance must be non positive, because if quoteBalance.amount > 0,
        // there must be 1) no negative pnl in perps, and 2) no liabilities in spot after above actions.
        // however, in this case the liquidatee must be healthy and cannot pass the health check at
        // the beginning.
        int128 insuranceCover = MathHelper.min(
            v.insurance,
            -quoteBalance.amount
        );
        if (insuranceCover > 0) {
            v.insurance -= insuranceCover;
            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                txn.liquidatee,
                insuranceCover
            );
        }
        if (v.insurance <= 0) {
            spotEngine.socializeSubaccount(txn.liquidatee);
        }
        v.insurance += lastLiquidationFees;
        insurance = v.insurance;
        return true;
    }
```

**File:** core/contracts/ClearinghouseLiq.sol (L598-607)
```text
    function liquidateSubaccountImpl(IEndpoint.LiquidateSubaccount calldata txn)
        external
    {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);
        require(txn.sender != txn.liquidatee, ERR_UNAUTHORIZED);
        require(isUnderMaintenance(txn.liquidatee), ERR_NOT_LIQUIDATABLE);
        require(
            txn.liquidatee != X_ACCOUNT && txn.liquidatee != N_ACCOUNT,
            ERR_NOT_LIQUIDATABLE
        );
```
