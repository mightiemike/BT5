### Title
Partial Per-Product Fee Tier Update Causes Zero Fees on Unconfigured Products — (`core/contracts/OffchainExchange.sol`)

### Summary
`updateTierFeeRates` sets the global `nonDefaultFeeTierMask` bit for a tier even when only a single product's fee rate is updated. `getTierFeeRateX18` uses that same per-tier bit to decide whether to return custom rates or the default 2 bps taker rate. Once the bit is set for a tier via a product-specific update, every other product in that tier returns an uninitialized `FeeRates{0, 0}` instead of the intended default, causing users in that tier to pay zero fees on all unconfigured products.

### Finding Description

`getTierFeeRateX18` uses a single per-tier bit in `nonDefaultFeeTierMask` to gate whether custom rates are returned:

```solidity
function getTierFeeRateX18(uint32 tier, uint32 productId)
    public view returns (FeeRates memory)
{
    if (nonDefaultFeeTierMask & (1 << tier) != 0) {
        return feeRates[tier][productId];   // returns {0,0} if never set
    }
    return FeeRates({ makerRateX18: 0, takerRateX18: 200_000_000_000_000 }); // 2 bps default
}
```

`updateTierFeeRates` has two branches: one that iterates all products (when `txn.productId == QUOTE_PRODUCT_ID`) and one that updates only a single product. Both branches unconditionally set the tier's bit in `nonDefaultFeeTierMask`:

```solidity
} else {
    feeRates[txn.tier][txn.productId] = FeeRates(txn.makerRateX18, txn.takerRateX18);
}
nonDefaultFeeTierMask |= uint128(1) << txn.tier;   // set regardless of scope
```

After a product-specific call, `nonDefaultFeeTierMask` marks the tier as "non-default." Any subsequent call to `getTierFeeRateX18` for a different product in that tier reads `feeRates[tier][otherProduct]`, which is an uninitialized mapping slot returning `FeeRates{makerRateX18: 0, takerRateX18: 0}`. The 2 bps default is permanently bypassed for all unconfigured products in that tier.

The inconsistency mirrors the reported vulnerability class exactly: one code path (the product-specific branch of `updateTierFeeRates`) uses a narrower scope of update, while the lookup path (`getTierFeeRateX18`) applies a broader criterion (the per-tier bit), causing the fee structure to diverge from intent.

### Impact Explanation

Any user assigned to a tier that has received at least one product-specific fee rate update pays **zero taker fees** on every other product in that tier. The protocol loses all taker fee revenue (normally 2 bps per trade) for those product/tier combinations. Because `applyFee` directly uses the returned `feeRate` with no floor check, the zero rate flows through to `orderInfo.fee = 0` and no fees are collected or credited to `collectedFees`.

### Likelihood Explanation

The trigger is a legitimate admin operation: setting a custom fee rate for a single product in a tier (e.g., giving a market-maker tier a rebate on one specific market). This is a normal operational action. Once done, all users in that tier silently receive zero fees on every other product. The admin has no indication this side-effect exists, and users have no incentive to report it.

### Recommendation

Decouple the per-product configuration state from the per-tier mask. Use a per-`(tier, productId)` bitmap or a sentinel value (e.g., a dedicated `bool isSet` field in `FeeRates`) so that `getTierFeeRateX18` can distinguish between "this product was explicitly set to 0" and "this product was never configured." Alternatively, when `updateTierFeeRates` is called with a specific `productId`, also populate all other existing products in that tier with the current default before setting the mask bit.

### Proof of Concept

1. Admin calls `updateTierFeeRates` with `tier = 5`, `productId = 2` (a specific perp market), `makerRateX18 = 0`, `takerRateX18 = 100_000_000_000_000` (1 bps). This sets `feeRates[5][2]` and sets bit 5 in `nonDefaultFeeTierMask`.
2. Admin assigns trader Alice to tier 5 via `updateFeeTier`.
3. Alice places a taker order on product 4 (a different spot market, never configured for tier 5).
4. `applyFee` calls `getUserFeeRateWithBuilder` → `getTierFeeRateX18(5, 4)`.
5. Since `nonDefaultFeeTierMask & (1 << 5) != 0`, the function returns `feeRates[5][4]` = `{0, 0}`.
6. `keepRateX18 = ONE - 0 = ONE`, so `newMeteredQuote = meteredQuote`, `orderInfo.fee = 0`.
7. Alice pays zero taker fees on product 4 instead of the intended 2 bps default. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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
