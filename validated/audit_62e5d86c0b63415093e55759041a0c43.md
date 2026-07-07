### Title
Hardcoded `MAX_DAILY_FUNDING_RATE` Constant Prevents Per-Product Funding Rate Adaptation, Exposing NLP Pool to Losses — (`core/contracts/PerpEngineState.sol`)

---

### Summary

The `MAX_DAILY_FUNDING_RATE` constant in `PerpEngineState.sol` is hardcoded at 2% per day and applied uniformly to every perp product. There is no mechanism to adjust this cap per-product or globally without a full contract upgrade. In extreme market conditions (e.g., a depeg event), this static cap prevents the funding rate from rising high enough to incentivize arbitrage, allowing the perp mark price to diverge persistently from the index price and exposing the NLP pool — the protocol's universal counterparty — to directional losses.

---

### Finding Description

The constant is declared at the top of `PerpEngineState.sol` with a developer comment explicitly acknowledging it is a placeholder:

```solidity
// we will want to config this later, but for now this is global and a percentage
int128 constant MAX_DAILY_FUNDING_RATE = 20000000000000000; // 0.02
``` [1](#0-0) 

It is consumed inside `updateStates`, which is called by the sequencer via the `Endpoint` on every state update cycle. The cap is applied to the raw `avgPriceDiffs` input before the funding payment is accumulated into the global cumulative funding trackers:

```solidity
int128 maxPriceDiff = MAX_DAILY_FUNDING_RATE.mul(indexPriceX18);
if (priceDiffX18.abs() > maxPriceDiff) {
    priceDiffX18 = (priceDiffX18 > 0)
        ? maxPriceDiff
        : -maxPriceDiff;
}
int128 paymentAmount = priceDiffX18.mul(dtX18).div(ONE_DAY_X18);
state.cumulativeFundingLongX18 += paymentAmount;
state.cumulativeFundingShortX18 += paymentAmount;
``` [2](#0-1) 

Unlike the `SpotEngine`, which exposes a per-product `Config` struct (containing `interestInflectionUtilX18`, `interestSmallCapX18`, `interestLargeCapX18`, etc.) that can be updated by the owner via `addOrUpdateProduct`, and unlike risk weights which can be updated via `updateRisk` in `BaseEngine`, the `MAX_DAILY_FUNDING_RATE` is a Solidity `constant` — it is baked into the bytecode and cannot be changed by any on-chain call, including owner-privileged ones. [3](#0-2) [4](#0-3) 

The `PerpEngine.addOrUpdateProduct` accepts only `sizeIncrement`, `minSize`, and a `RiskStore` — there is no field for a per-product funding rate cap: [5](#0-4) 

---

### Impact Explanation

The funding rate is the primary mechanism that keeps the perp mark price anchored to the index price. When the mark price diverges, the funding rate creates a cost for the side that is "wrong" (e.g., longs when mark > index), incentivizing arbitrageurs to close those positions and restore parity.

If the divergence exceeds what a 2% daily cap can correct — as happens during a depeg event where an asset loses 10–50% of its value rapidly — the cap becomes the binding constraint. The funding rate cannot rise above 2%/day regardless of how large the divergence is. Arbitrageurs are under-incentivized, the divergence persists, and traders on the profitable side of the divergence can hold positions against the NLP pool at a subsidized cost.

Concretely, the NLP pool's `availableSettle` (the liquidity available for PnL settlement) is drained as profitable traders call `settlePnl`, converting their `vQuoteBalance` gains into real quote tokens: [6](#0-5) 

The NLP pool has no recourse because the funding rate — the only self-correcting mechanism — is capped at a value that is insufficient for the market condition.

---

### Likelihood Explanation

Medium. The trigger requires a significant and sustained mark/index divergence beyond 2%/day. This is uncommon in normal conditions but has occurred repeatedly in DeFi history (UST depeg May 2022, USDC depeg March 2023, LUNA collapse). The protocol lists perpetual markets as a core product, and any listed asset is a candidate. No privileged access is required; any trader can open a position through the standard `Endpoint` entry point.

---

### Recommendation

Replace the global `constant` with a per-product configurable field. Add a `maxDailyFundingRateX18` field to the `PerpEngine` product config (analogous to `SpotEngine`'s `Config` struct), set it during `addOrUpdateProduct`, and read it inside `updateStates` instead of the hardcoded constant. Include a reasonable upper bound (e.g., 10% per day) to prevent misconfiguration. This mirrors the pattern already established for `SpotEngine` interest rate parameters.

---

### Proof of Concept

**Setup**: A perp market for asset X is live. The NLP pool is the counterparty. `MAX_DAILY_FUNDING_RATE = 0.02` (2%/day).

**Event**: Asset X depegs. Its spot price drops 30% in 24 hours. The perp mark price lags because the funding rate can only push it 2%/day.

**Exploitation**:
1. A trader opens a large short position via `Endpoint` → `OffchainExchange` → `PerpEngine.updateBalance`. The short is profitable because mark > index (mark has not caught up to the crash).
2. The sequencer calls `PerpEngine.updateStates` with `avgPriceDiffs` reflecting the full 30% divergence. The cap truncates this to 2%/day.
3. The trader's `vQuoteBalance` accumulates gains at the capped rate. The NLP pool's `availableSettle` decreases correspondingly.
4. The trader calls `settlePnl` to realize gains as quote tokens. The NLP pool's real quote balance is reduced.
5. This repeats over multiple days. The NLP pool loses funds at a rate proportional to the uncorrected divergence, while the funding rate cap prevents the market from self-correcting. [7](#0-6)

### Citations

**File:** core/contracts/PerpEngineState.sol (L10-11)
```text
// we will want to config this later, but for now this is global and a percentage
int128 constant MAX_DAILY_FUNDING_RATE = 20000000000000000; // 0.02
```

**File:** core/contracts/PerpEngineState.sol (L103-144)
```text
    function updateStates(uint128 dt, int128[] calldata avgPriceDiffs)
        external
        onlyEndpoint
    {
        int128 dtX18 = int128(dt).fromInt();
        for (uint32 i = 0; i < avgPriceDiffs.length; i++) {
            uint32 productId = productIds[i];
            State memory state = states[productId];
            if (state.openInterest == 0) {
                continue;
            }
            require(dt < 7 * SECONDS_PER_DAY, ERR_INVALID_TIME);
            {
                int128 indexPriceX18 = _risk(productId).priceX18;

                // cap this price diff
                int128 priceDiffX18 = avgPriceDiffs[i];

                int128 maxPriceDiff = MAX_DAILY_FUNDING_RATE.mul(indexPriceX18);

                if (priceDiffX18.abs() > maxPriceDiff) {
                    // Proper sign
                    priceDiffX18 = (priceDiffX18 > 0)
                        ? maxPriceDiff
                        : -maxPriceDiff;
                }

                int128 paymentAmount = priceDiffX18.mul(dtX18).div(ONE_DAY_X18);
                state.cumulativeFundingLongX18 += paymentAmount;
                state.cumulativeFundingShortX18 += paymentAmount;

                emit FundingPayment(
                    productId,
                    dt,
                    state.openInterest,
                    paymentAmount
                );
            }
            _setState(productId, state);
            emit PriceQuery(productId);
        }
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

**File:** core/contracts/BaseEngine.sol (L278-290)
```text
    function updateRisk(uint32 productId, RiskHelper.RiskStore memory riskStore)
        external
        onlyOwner
    {
        require(
            riskStore.longWeightInitial <= riskStore.longWeightMaintenance &&
                riskStore.shortWeightInitial >=
                riskStore.shortWeightMaintenance,
            ERR_BAD_PRODUCT_CONFIG
        );

        _risk().value[productId] = riskStore;
    }
```

**File:** core/contracts/PerpEngine.sol (L33-58)
```text
    function addOrUpdateProduct(
        uint32 productId,
        int128 sizeIncrement,
        int128 minSize,
        RiskHelper.RiskStore calldata riskStore
    ) public onlyOwner {
        bool isNewProduct = _addOrUpdateProduct(
            productId,
            QUOTE_PRODUCT_ID,
            sizeIncrement,
            minSize,
            riskStore
        );

        if (isNewProduct) {
            _setState(
                productId,
                State({
                    cumulativeFundingLongX18: 0,
                    cumulativeFundingShortX18: 0,
                    availableSettle: 0,
                    openInterest: 0
                })
            );
        }
    }
```

**File:** core/contracts/PerpEngine.sol (L77-105)
```text
    function settlePnl(bytes32 subaccount, uint256 productIds)
        external
        returns (int128)
    {
        _assertInternal();
        int128 totalSettled = 0;

        while (productIds != 0) {
            uint32 productId = uint32(productIds & ((1 << 32) - 1));
            // otherwise it means the product is a spot.
            if (productId % 2 == 0) {
                (
                    int128 canSettle,
                    State memory state,
                    Balance memory balance
                ) = getSettlementState(productId, subaccount);

                state.availableSettle -= canSettle;
                balance.vQuoteBalance -= canSettle;

                totalSettled += canSettle;

                _setState(productId, state);
                _setBalanceAndUpdateBitmap(productId, subaccount, balance);
            }
            productIds >>= 32;
        }
        return totalSettled;
    }
```
