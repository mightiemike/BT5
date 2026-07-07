### Title
New Product Listing Leaves Non-Default Fee Tiers at Zero Rate, Causing Protocol Fee Loss - (`File: core/contracts/OffchainExchange.sol`)

### Summary
When a new spot or perp product is added to the protocol after one or more non-default fee tiers have already been configured, `feeRates[tier][newProductId]` remains at the Solidity default of `0` for every non-default tier. Because `getTierFeeRateX18` unconditionally returns the stored mapping value for any tier whose bit is set in `nonDefaultFeeTierMask`, users assigned to those tiers pay **zero protocol fees** on the newly listed product until the admin separately calls `updateTierFeeRates` for it.

---

### Finding Description

`OffchainExchange.sol` stores per-tier, per-product fee rates in a nested mapping and tracks which tiers have been explicitly configured via a bitmask:

```
mapping(uint32 => mapping(uint32 => FeeRates)) internal feeRates;
uint128 internal nonDefaultFeeTierMask;
``` [1](#0-0) [2](#0-1) 

`getTierFeeRateX18` branches on whether the tier's bit is set:

```solidity
function getTierFeeRateX18(uint32 tier, uint32 productId) public view returns (FeeRates memory) {
    if (nonDefaultFeeTierMask & (1 << tier) != 0) {
        return feeRates[tier][productId];   // ← returns 0,0 for any product not yet configured
    }
    return FeeRates({ makerRateX18: 0, takerRateX18: 200_000_000_000_000 }); // 2 bps default
}
``` [3](#0-2) 

`updateTierFeeRates` sets rates only for products **already registered** at the time of the call, then permanently marks the tier as non-default:

```solidity
if (txn.productId == QUOTE_PRODUCT_ID) {
    for (uint32 i = 0; i < spotProductIds.length; i++) { ... feeRates[txn.tier][spotProductIds[i]] = ...; }
    for (uint32 i = 0; i < perpProductIds.length; i++) { ... feeRates[txn.tier][perpProductIds[i]] = ...; }
}
nonDefaultFeeTierMask |= uint128(1) << txn.tier;   // bit is set permanently
``` [4](#0-3) 

When a new product is subsequently added via `addOrUpdateProducts` → `spotEngine.addOrUpdateProduct` / `perpEngine.addOrUpdateProduct`, no corresponding entry is written into `feeRates[tier][newProductId]` for any existing non-default tier. [5](#0-4) 

From that point, any user whose `feeTiers[user]` maps to a non-default tier will have `getUserFeeRateWithBuilder` return `feeRate = 0` for the new product:

```solidity
FeeRates memory userFeeRates = getTierFeeRateX18(feeTier, productId);
int128 feeRate = taker ? userFeeRates.takerRateX18 : userFeeRates.makerRateX18;
``` [6](#0-5) 

In `applyFee`, a `feeRate` of `0` means `keepRateX18 = ONE`, so `newMeteredQuote == meteredQuote` and `orderInfo.fee = 0`:

```solidity
int128 keepRateX18 = ONE - feeInfo.feeRate;
int128 newMeteredQuote = (meteredQuote > 0)
    ? meteredQuote.mul(keepRateX18)
    : meteredQuote.div(keepRateX18);
orderInfo.fee = meteredQuote - newMeteredQuote;   // = 0 when feeRate = 0
``` [7](#0-6) 

`marketInfo[productId].collectedFees` is therefore never incremented for these trades, and `dumpFees` transfers nothing to `X_ACCOUNT` for the new product from these users. [8](#0-7) 

---

### Impact Explanation

Protocol fee revenue is permanently lost for every trade executed by a non-default-tier user on a newly listed product before the admin calls `updateTierFeeRates` for that product. The corrupted state is `marketInfo[newProductId].collectedFees`, which is understated by the full fee amount that should have been collected. Because `dumpFees` sweeps only what is in `collectedFees`, the shortfall is irrecoverable after the fact.

---

### Likelihood Explanation

Medium. Non-default fee tiers are a live protocol feature used for VIP/high-volume traders. New products are listed periodically. The gap between `addOrUpdateProducts` and a subsequent `updateTierFeeRates` call for the new product is a realistic operational window. High-volume traders (precisely those on non-default tiers) are the most likely to trade a newly listed product immediately after launch, maximizing the fee loss.

---

### Recommendation

When `addOrUpdateProducts` registers a new product, iterate over all bits set in `nonDefaultFeeTierMask` and initialize `feeRates[tier][newProductId]` to a safe default (e.g., the same rates as tier 0's default, or a protocol-configured baseline). Alternatively, modify `getTierFeeRateX18` to fall back to the hardcoded default when `feeRates[tier][productId]` is the zero struct, even for non-default tiers — though this requires a sentinel to distinguish "explicitly set to zero" from "never set."

---

### Proof of Concept

1. Admin calls `updateTierFeeRates` with `tier=1`, `productId=QUOTE_PRODUCT_ID`, `takerRateX18=500_000_000_000_000` (5 bps). This sets rates for all currently registered products and sets bit 1 in `nonDefaultFeeTierMask`.
2. Admin calls `addOrUpdateProducts` to list a new spot product with `productId=99`. `feeRates[1][99]` is never written; it remains `{0, 0}`.
3. VIP user Alice has `feeTiers[alice] = 1`.
4. Alice submits a taker order on product 99 for $100,000 notional.
5. `getTierFeeRateX18(1, 99)` returns `{makerRateX18: 0, takerRateX18: 0}` because bit 1 is set in `nonDefaultFeeTierMask` and `feeRates[1][99] == {0,0}`.
6. `applyFee` computes `orderInfo.fee = 0`. Alice pays no fees. Protocol loses ~$50 in fees on this single trade (at 5 bps on $100k).
7. This repeats for every non-default-tier user on product 99 until the admin separately calls `updateTierFeeRates` for it. [3](#0-2) [9](#0-8)

### Citations

**File:** core/contracts/OffchainExchange.sol (L35-36)
```text
    // tier -> productId -> fee rates
    mapping(uint32 => mapping(uint32 => FeeRates)) internal feeRates;
```

**File:** core/contracts/OffchainExchange.sol (L61-61)
```text
    uint128 internal nonDefaultFeeTierMask;
```

**File:** core/contracts/OffchainExchange.sol (L498-506)
```text
        uint32 feeTier = feeTiers[address(uint160(bytes20(sender)))];
        if (feeTier < builder.defaultFeeTier) {
            feeTier = builder.defaultFeeTier;
        }
        FeeRates memory userFeeRates = getTierFeeRateX18(feeTier, productId);
        int128 feeRate = taker
            ? userFeeRates.takerRateX18
            : userFeeRates.makerRateX18;
        return FeeInfo(feeRate, builderId, builderFeeRate);
```

**File:** core/contracts/OffchainExchange.sol (L556-560)
```text
        int128 keepRateX18 = ONE - feeInfo.feeRate;
        int128 newMeteredQuote = (meteredQuote > 0)
            ? meteredQuote.mul(keepRateX18)
            : meteredQuote.div(keepRateX18);
        orderInfo.fee = meteredQuote - newMeteredQuote;
```

**File:** core/contracts/OffchainExchange.sol (L891-930)
```text
    function dumpFees() external onlyEndpoint {
        // loop over all spot and perp product ids
        uint32[] memory productIds = spotEngine.getProductIds();

        for (uint32 i = 1; i < productIds.length; i++) {
            uint32 productId = productIds[i];
            MarketInfoStore memory market = marketInfo[productId];
            if (market.collectedFees == 0) {
                continue;
            }

            spotEngine.updateBalance(
                quoteIds[productId],
                X_ACCOUNT,
                market.collectedFees
            );

            market.collectedFees = 0;
            marketInfo[productId] = market;
        }

        productIds = perpEngine.getProductIds();

        for (uint32 i = 0; i < productIds.length; i++) {
            uint32 productId = productIds[i];
            MarketInfoStore memory market = marketInfo[productId];
            if (market.collectedFees == 0) {
                continue;
            }

            perpEngine.updateBalance(
                productId,
                X_ACCOUNT,
                0,
                market.collectedFees
            );

            market.collectedFees = 0;
            marketInfo[productId] = market;
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

**File:** core/contracts/ContractOwner.sol (L147-182)
```text
    function addOrUpdateProducts(
        uint32[] memory spotIds,
        uint32[] memory perpIds
    ) external onlyOwner {
        for (uint256 i = 0; i < rawSpotAddOrUpdateProductCalls.length; i++) {
            SpotAddOrUpdateProductCall memory call = abi.decode(
                rawSpotAddOrUpdateProductCalls[i],
                (SpotAddOrUpdateProductCall)
            );
            require(spotIds[i] == call.productId, "spot mismatch");
            spotEngine.addOrUpdateProduct(
                call.productId,
                call.quoteId,
                call.sizeIncrement,
                call.minSize,
                call.config,
                call.riskStore
            );
        }
        delete rawSpotAddOrUpdateProductCalls;

        for (uint256 i = 0; i < rawPerpAddOrUpdateProductCalls.length; i++) {
            PerpAddOrUpdateProductCall memory call = abi.decode(
                rawPerpAddOrUpdateProductCalls[i],
                (PerpAddOrUpdateProductCall)
            );
            require(perpIds[i] == call.productId, "perp mismatch");
            perpEngine.addOrUpdateProduct(
                call.productId,
                call.sizeIncrement,
                call.minSize,
                call.riskStore
            );
        }
        delete rawPerpAddOrUpdateProductCalls;
    }
```
