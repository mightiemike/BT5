### Title
Uninitialized Fee Rates for New Products Under Pre-Existing Custom Fee Tiers Cause Zero-Fee Trading — (`core/contracts/OffchainExchange.sol`)

---

### Summary

`OffchainExchange.getTierFeeRateX18()` uses `nonDefaultFeeTierMask` to decide whether to return custom or default fee rates. Once a custom tier's bit is set in that mask, the function unconditionally returns `feeRates[tier][productId]` for every product — including products added *after* the tier was configured, for which `feeRates[tier][productId]` was never written and therefore remains `{0, 0}`. Any trader assigned that custom tier pays zero taker fees on the new product, silently draining protocol fee revenue with no revert.

---

### Finding Description

`updateTierFeeRates` sets fee rates for all *currently registered* products when called with `productId == QUOTE_PRODUCT_ID`, and simultaneously stamps the tier's bit into `nonDefaultFeeTierMask`: [1](#0-0) 

After this call, `nonDefaultFeeTierMask & (1 << tier) != 0` is permanently true for that tier. When a new product is later added via `addOrUpdateProduct`, no code path writes `feeRates[tier][newProductId]` for any pre-existing custom tier. The mapping entry stays at its Solidity default: `FeeRates({makerRateX18: 0, takerRateX18: 0})`.

`getTierFeeRateX18` then reads this uninitialized entry and returns it verbatim: [2](#0-1) 

The mask check at line 938 passes (the tier *was* configured), so the function never falls through to the default `takerRateX18 = 200_000_000_000_000` branch. It returns `{0, 0}` instead.

`applyFee` consumes this zero rate without reverting: [3](#0-2) 

With `feeInfo.feeRate = 0`, `keepRateX18 = ONE`, `newMeteredQuote = meteredQuote`, and `orderInfo.fee = 0`. The taker is charged nothing. The protocol's `collectedFees` for the new product is never incremented for these trades.

This is structurally identical to the Beanstalk bug: a configuration variable (`feeRates[tier][productId]`) is read from storage before it has been initialized for the specific (tier, product) pair, the uninitialized zero value is silently used in accounting, and no revert occurs.

---

### Impact Explanation

Every taker whose address has been assigned a non-zero custom fee tier pays **zero taker fees** on any product added after the tier was configured. The protocol's `collectedFees` accumulator for those products is never credited for those trades, permanently losing fee revenue. The corruption is silent — no event, no revert, no observable on-chain signal distinguishes a legitimately zero-fee trade from this miscounted one.

---

### Likelihood Explanation

Custom fee tiers are a normal, documented protocol feature (`updateFeeTier` / `updateTierFeeRates`). New products are added regularly. The window between "tier configured" and "new product added" is permanent: once a product is listed without updating the tier's fee rates, every subsequent trade by a custom-tier user on that product is affected. No special attacker capability is required beyond holding a subaccount that has been assigned a custom tier — a condition that is set by the sequencer/admin but is a routine operational state for market makers and institutional participants.

---

### Recommendation

When `addOrUpdateProduct` registers a new product, iterate over all bits set in `nonDefaultFeeTierMask` and write a sensible default (e.g., the protocol default `{0, 200_000_000_000_000}`) into `feeRates[tier][newProductId]` for each configured tier. Alternatively, change `getTierFeeRateX18` to treat a zero `takerRateX18` under a non-default tier as "not configured for this product" and fall back to the default rate:

```solidity
function getTierFeeRateX18(uint32 tier, uint32 productId)
    public view returns (FeeRates memory)
{
    if (nonDefaultFeeTierMask & (1 << tier) != 0) {
        FeeRates memory rates = feeRates[tier][productId];
        if (rates.takerRateX18 != 0 || rates.makerRateX18 != 0) {
            return rates;
        }
    }
    return FeeRates({makerRateX18: 0, takerRateX18: 200_000_000_000_000});
}
```

---

### Proof of Concept

1. Admin calls `updateTierFeeRates` with `productId = QUOTE_PRODUCT_ID`, `tier = 1`, `takerRateX18 = 1e14` (1 bp). This writes rates for all current products and sets `nonDefaultFeeTierMask |= 1 << 1`. [4](#0-3) 

2. Admin calls `SpotEngine.addOrUpdateProduct` or `PerpEngine.addOrUpdateProduct` to list a new product (e.g., `productId = 7`). `feeRates[1][7]` is never written; it remains `{0, 0}`. [5](#0-4) 

3. Admin calls `updateFeeTier(userAddress, 1)` (or this was done earlier). The user's address maps to tier 1. [6](#0-5) 

4. User submits a taker order on product 7. `getUserFeeRateWithBuilder` calls `getTierFeeRateX18(1, 7)`. The mask check passes (`nonDefaultFeeTierMask & 2 != 0`), so `feeRates[1][7] = {0, 0}` is returned. [7](#0-6) 

5. `applyFee` computes `fee = 0`. The taker pays nothing. `collectedFees` for product 7 is not incremented for this trade. The protocol loses the expected 1 bp fee revenue on every such trade, indefinitely. [8](#0-7)

### Citations

**File:** core/contracts/OffchainExchange.sol (L497-507)
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
    }
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

**File:** core/contracts/OffchainExchange.sol (L952-960)
```text
    function updateFeeTier(address user, uint32 newTier) external {
        require(msg.sender == address(clearinghouse), ERR_UNAUTHORIZED);
        if (newTier != 0 && !addressTouched[user]) {
            addressTouched[user] = true;
            customFeeAddresses.push(user);
        }
        feeTiers[user] = newTier;
        emit FeeTierUpdate(user, newTier);
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

**File:** core/contracts/SpotEngine.sol (L68-97)
```text
    function addOrUpdateProduct(
        uint32 productId,
        uint32 quoteId,
        int128 sizeIncrement,
        int128 minSize,
        Config calldata config,
        RiskHelper.RiskStore calldata riskStore
    ) public onlyOwner {
        bool isNewProduct = _addOrUpdateProduct(
            productId,
            quoteId,
            sizeIncrement,
            minSize,
            riskStore
        );
        configs[productId] = config;

        if (isNewProduct) {
            require(productId != QUOTE_PRODUCT_ID);
            _setState(
                productId,
                State({
                    cumulativeDepositsMultiplierX18: ONE,
                    cumulativeBorrowsMultiplierX18: ONE,
                    totalDepositsNormalized: 0,
                    totalBorrowsNormalized: 0
                })
            );
        }
    }
```
