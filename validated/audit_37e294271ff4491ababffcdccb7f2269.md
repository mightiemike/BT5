### Title
`nonDefaultFeeTierMask` Set Globally But `feeRates` Only Populated for Existing Products Causes Zero Fees on New Products for Non-Default Tiers — (`File: core/contracts/OffchainExchange.sol`)

---

### Summary

In `OffchainExchange`, a global tier-level bitmask (`nonDefaultFeeTierMask`) is set when fee rates are updated across all products. However, the actual per-product fee rates are only written for products that exist at the time of the update. When new products are added later, `getTierFeeRateX18` returns zero fees for those tiers on the new products, because the mask already marks the tier as non-default, bypassing the hardcoded 2 bps default.

---

### Finding Description

`updateTierFeeRates` handles a special case: when `txn.productId == QUOTE_PRODUCT_ID`, it iterates over all currently registered spot and perp products and writes the same fee rates to each: [1](#0-0) 

After populating `feeRates[txn.tier][productId]` for every currently known product, it unconditionally sets the tier-level flag: [2](#0-1) 

`getTierFeeRateX18` uses this mask as a binary switch: if the tier bit is set, it returns `feeRates[tier][productId]` for **any** product, including ones that did not exist when the mask was set: [3](#0-2) 

When a new product is added via `SpotEngine.addOrUpdateProduct` or `PerpEngine.addOrUpdateProduct` after the mask was set, `feeRates[tier][newProductId]` is never initialized and remains zero (Solidity default). `getTierFeeRateX18` returns `FeeRates({makerRateX18: 0, takerRateX18: 0})` for that tier on the new product.

In `applyFee`, a zero `feeRate` means `keepRateX18 = ONE - 0 = ONE`, so `orderInfo.fee = meteredQuote - meteredQuote.mul(ONE) = 0`: [4](#0-3) 

The trader pays no fees on the new product for that tier.

This is structurally identical to the external report's root cause: a shared global configuration parameter (`nonDefaultFeeTierMask` at the tier level) is applied uniformly across all products, but the per-product fee data (`feeRates[tier][productId]`) is only populated for products existing at configuration time. New products inherit the global flag but not the actual rates — the same architectural flaw as shared `navProvider` being applied to all vaults regardless of asset type.

---

### Impact Explanation

Traders assigned to any non-default fee tier trade on newly added products with zero taker and maker fees. The protocol loses all fee revenue on those products for those tiers. If tier 0 is also marked non-default via a global update, every trader pays zero fees on new products. The `dumpFees` and `claimSequencerFees` flows will collect nothing from those markets, directly reducing protocol revenue.

---

### Likelihood Explanation

The trigger requires two sequential admin operations (a global fee rate update followed by a new product listing), both of which are routine protocol lifecycle events. Once those two operations occur, any trader with a non-default fee tier exploits the zero-fee condition simply by trading normally — no special knowledge or privileged access is required. The combination of normal admin operations and normal user trading makes this a realistic scenario.

---

### Recommendation

In `getTierFeeRateX18`, add a fallback: if the tier is marked non-default but the stored `takerRateX18` for the specific product is zero, return the hardcoded default rate. Alternatively, when a new product is added, iterate over all tiers present in `nonDefaultFeeTierMask` and initialize `feeRates[tier][newProductId]` to the rates of an existing product or a protocol-defined default.

---

### Proof of Concept

1. Sequencer submits `updateTierFeeRates` with `productId = QUOTE_PRODUCT_ID`, `tier = 1`, `takerRateX18 = 500_000_000_000_000` (5 bps).
   - `feeRates[1][existingSpotProduct]` and `feeRates[1][existingPerpProduct]` are set to 5 bps.
   - `nonDefaultFeeTierMask |= 2` is set.
2. Owner calls `SpotEngine.addOrUpdateProduct(newProductId, ...)`.
   - `feeRates[1][newProductId]` is never written → remains `FeeRates(0, 0)`.
3. Trader with `feeTiers[trader] = 1` submits a taker order on `newProductId`.
   - `getUserFeeRateWithBuilder` calls `getTierFeeRateX18(1, newProductId)`.
   - `nonDefaultFeeTierMask & (1 << 1) != 0` → returns `feeRates[1][newProductId]` = `FeeRates(0, 0)`.
   - `applyFee` computes `keepRateX18 = ONE`, `fee = 0`.
   - Trader executes at full quote value with zero fees.
   - `updateCollectedFees` adds 0 to `market.collectedFees`; `dumpFees` transfers nothing for this product. [5](#0-4) [3](#0-2) [6](#0-5)

### Citations

**File:** core/contracts/OffchainExchange.sol (L509-571)
```text
    function applyFee(
        uint32 productId,
        OrderInfo memory orderInfo,
        MarketInfo memory market,
        int128 alreadyMatched, // in quote
        uint128 appendix,
        bool taker
    ) internal {
        // X account is passthrough for trading and incurs
        // no fees
        if (orderInfo.sender == X_ACCOUNT) {
            return;
        }
        int128 matchQuote = orderInfo.quoteDelta;
        int128 meteredQuote = 0;
        if (taker) {
            // flat minimum fee
            if (alreadyMatched == 0) {
                meteredQuote += market.minSize;
                if (matchQuote < 0) {
                    meteredQuote = -meteredQuote;
                }
            }

            // exclude the portion on [0, self.min_size) for match_quote and
            // add to metered_quote
            // fee is only applied on [minSize, quote_amount)
            int128 feeApplied = MathHelper.abs(alreadyMatched + matchQuote) -
                market.minSize;
            feeApplied = MathHelper.min(feeApplied, matchQuote.abs());
            if (feeApplied > 0) {
                if (matchQuote < 0) {
                    feeApplied = -feeApplied;
                }
                meteredQuote += feeApplied;
            }
        } else {
            // for maker rebates things stay the same
            meteredQuote += matchQuote;
        }
        FeeInfo memory feeInfo = getUserFeeRateWithBuilder(
            orderInfo.sender,
            productId,
            appendix,
            taker
        );

        int128 keepRateX18 = ONE - feeInfo.feeRate;
        int128 newMeteredQuote = (meteredQuote > 0)
            ? meteredQuote.mul(keepRateX18)
            : meteredQuote.div(keepRateX18);
        orderInfo.fee = meteredQuote - newMeteredQuote;
        orderInfo.builderFee = matchQuote.abs().mul(feeInfo.builderFeeRate);
        orderInfo.quoteDelta =
            orderInfo.quoteDelta -
            orderInfo.fee -
            orderInfo.builderFee;
        if (orderInfo.builderFee > 0) {
            collectedBuilderFee[market.quoteId][feeInfo.builderId] += orderInfo
                .builderFee;
            emitBuilderEvent(orderInfo, feeInfo.builderId, productId);
        }
    }
```

**File:** core/contracts/OffchainExchange.sol (L933-946)
```text
    function getTierFeeRateX18(uint32 tier, uint32 productId)
        public
        view
        returns (FeeRates memory)
    {
        if (nonDefaultFeeTierMask & (1 << tier) != 0) {
            return feeRates[tier][productId];
        }
        return
            FeeRates({
                makerRateX18: 0,
                takerRateX18: 200_000_000_000_000 // 2 bps
            });
    }
```

**File:** core/contracts/OffchainExchange.sol (L962-991)
```text
    function updateTierFeeRates(IEndpoint.UpdateTierFeeRates memory txn)
        external
        onlyEndpoint
    {
        if (txn.productId == QUOTE_PRODUCT_ID) {
            uint32[] memory spotProductIds = spotEngine.getProductIds();
            uint32[] memory perpProductIds = perpEngine.getProductIds();
            for (uint32 i = 0; i < spotProductIds.length; i++) {
                if (spotProductIds[i] == QUOTE_PRODUCT_ID) {
                    continue;
                }
                feeRates[txn.tier][spotProductIds[i]] = FeeRates(
                    txn.makerRateX18,
                    txn.takerRateX18
                );
            }
            for (uint32 i = 0; i < perpProductIds.length; i++) {
                feeRates[txn.tier][perpProductIds[i]] = FeeRates(
                    txn.makerRateX18,
                    txn.takerRateX18
                );
            }
        } else {
            feeRates[txn.tier][txn.productId] = FeeRates(
                txn.makerRateX18,
                txn.takerRateX18
            );
        }
        nonDefaultFeeTierMask |= uint128(1) << txn.tier;
    }
```
