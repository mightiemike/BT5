### Title
Hardcoded Zero Fee Rate for New Products After `updateTierFeeRates` Disregards the Intended Fee Schedule — (File: `core/contracts/OffchainExchange.sol`)

---

### Summary

`OffchainExchange.sol` contains a fee-tier system where `updateTierFeeRates` can update fee rates for all existing products by passing `QUOTE_PRODUCT_ID`. This sets the `nonDefaultFeeTierMask` bit for the affected tier. However, any product added **after** this call will have a zero-initialized `feeRates[tier][newProductId]` entry. Because the tier's bit is already set in `nonDefaultFeeTierMask`, `getTierFeeRateX18` bypasses the hardcoded 2 bps fallback and returns `{0, 0}` for the new product — silently zeroing out taker fees for all traders on that product.

---

### Finding Description

`getTierFeeRateX18` uses `nonDefaultFeeTierMask` to decide whether to return the stored `feeRates[tier][productId]` or the hardcoded default of 2 bps:

```solidity
// OffchainExchange.sol lines 933–946
function getTierFeeRateX18(uint32 tier, uint32 productId)
    public view returns (FeeRates memory)
{
    if (nonDefaultFeeTierMask & (1 << tier) != 0) {
        return feeRates[tier][productId];
    }
    return FeeRates({
        makerRateX18: 0,
        takerRateX18: 200_000_000_000_000 // 2 bps
    });
}
```

`updateTierFeeRates` sets the mask bit and populates `feeRates` only for products that exist at call time:

```solidity
// OffchainExchange.sol lines 962–991
function updateTierFeeRates(IEndpoint.UpdateTierFeeRates memory txn)
    external onlyEndpoint
{
    if (txn.productId == QUOTE_PRODUCT_ID) {
        // iterates only currently registered products
        for (uint32 i = 0; i < spotProductIds.length; i++) { ... }
        for (uint32 i = 0; i < perpProductIds.length; i++) { ... }
    }
    nonDefaultFeeTierMask |= uint128(1) << txn.tier;  // bit is set permanently
}
```

Once the bit is set, `getTierFeeRateX18` will always read from `feeRates[tier][productId]` for that tier. For any product registered after the call, `feeRates[tier][newProductId]` is Solidity-default zero (`{0, 0}`). The hardcoded 2 bps fallback is permanently bypassed for that tier, and the new product silently receives a 0 taker fee rate.

This is structurally identical to the FrankenDAO bug: a configurable parameter (`monsterMultiplier` / the fee schedule) is updated through a setter, but a separate code path uses a hardcoded value (`/2` / `{0, 0}`) that does not reflect the update.

---

### Impact Explanation

**Accounting corruption — protocol fee revenue loss.**

For tier 0 (the default tier assigned to every user who has not been explicitly upgraded), all traders on any product added after a `updateTierFeeRates(tier=0, productId=QUOTE_PRODUCT_ID)` call pay **zero taker fees** instead of the intended rate. `applyFee` → `getUserFeeRateWithBuilder` → `getTierFeeRateX18` is the live execution path for every matched order, so the corrupted rate is applied to every trade on the affected product from the moment it is listed.

The protocol permanently loses fee revenue on those products until the issue is manually corrected by calling `updateTierFeeRates` again specifically for the new product.

---

### Likelihood Explanation

**Medium.** Calling `updateTierFeeRates` with `QUOTE_PRODUCT_ID` is a routine administrative action (e.g., adjusting the global taker fee from 2 bps to 3 bps). Adding new products is also a routine operational action. Both are expected to occur in normal protocol lifecycle. No malicious intent is required; the bug manifests as an unintended consequence of two ordinary sequenced operations. This mirrors the FrankenDAO finding where governance passing a `setMonsterMultiplier` proposal was rated medium likelihood.

---

### Recommendation

When `addOrUpdateProduct` registers a new product, initialize `feeRates[tier][newProductId]` for every tier already present in `nonDefaultFeeTierMask` using the rates stored for an existing product of the same type, or store a per-tier "default rate" state variable that `getTierFeeRateX18` falls back to instead of the hardcoded literal `200_000_000_000_000`.

Alternatively, modify `getTierFeeRateX18` to treat a zero-initialized `feeRates` entry as "not set" and fall back to the hardcoded default even when the tier bit is present in `nonDefaultFeeTierMask`.

---

### Proof of Concept

1. Sequencer submits `UpdateTierFeeRates(tier=0, productId=QUOTE_PRODUCT_ID, makerRateX18=0, takerRateX18=300_000_000_000_000)`.
2. `updateTierFeeRates` iterates all currently registered products and writes `feeRates[0][existingId] = {0, 3e14}`. Sets `nonDefaultFeeTierMask |= 1`.
3. Owner calls `addOrUpdateProduct` to list a new spot product with `productId = 5`. `feeRates[0][5]` is never written; it remains `{0, 0}`.
4. A trader (default tier 0) submits a taker order on product 5. `applyFee` calls `getUserFeeRateWithBuilder` → `getTierFeeRateX18(0, 5)`.
5. `nonDefaultFeeTierMask & 1 != 0` → true → returns `feeRates[0][5]` = `{0, 0}`.
6. `keepRateX18 = ONE - 0 = ONE`. `newMeteredQuote = meteredQuote`. `orderInfo.fee = 0`.
7. The trader pays **zero taker fees** on every trade on product 5, and the protocol collects nothing. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** core/contracts/OffchainExchange.sol (L477-506)
```text
    function getUserFeeRateWithBuilder(
        bytes32 sender,
        uint32 productId,
        uint128 appendix,
        bool taker
    ) internal view returns (FeeInfo memory) {
        (uint32 builderId, int128 builderFeeRate) = _builderInfo(appendix);
        Builder memory builder;
        if (builderId != 0) {
            builder = builders[builderId];
            if (
                builder.owner == address(0) ||
                builderFeeRate > builder.highestFeeRate ||
                builderFeeRate < builder.lowestFeeRate
            ) {
                revert(ERR_INVALID_BUILDER);
            }
        } else if (builderFeeRate != 0) {
            revert(ERR_INVALID_BUILDER);
        }

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

**File:** core/contracts/OffchainExchange.sol (L549-565)
```text
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
