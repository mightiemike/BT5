### Title
Builder Fees from Perp Trades Permanently Locked Due to Wrong Mapping Key in `claimBuilderFee` — (`core/contracts/OffchainExchange.sol`)

---

### Summary

`claimBuilderFee` reads builder fee balances using `productId` as the first mapping key, but `applyFee` stores them using `quoteId`. For perp products, the function never iterates over perp product IDs at all. As a result, all builder fees accumulated from perp trades are permanently locked in the contract and can never be claimed.

---

### Finding Description

`collectedBuilderFee` is declared with the comment `quoteId -> builder -> amount`: [1](#0-0) 

In `applyFee`, fees are stored under `market.quoteId`: [2](#0-1) 

In `claimBuilderFee`, the function iterates only over `spotEngine.getProductIds()` and reads using `productId` as the first key — not `quoteId`: [3](#0-2) 

There are two compounding errors:

1. **Wrong key**: fees are stored at `collectedBuilderFee[quoteId][builderId]` but read at `collectedBuilderFee[productId][builderId]`. For standard spot products where `quoteId == QUOTE_PRODUCT_ID == 0`, all fees accumulate at index `0`. The loop happens to read index `0` when `productId == 0`, so spot fees are incidentally claimed correctly. But for any product where `productId != quoteId`, the read returns `0`.

2. **Missing perp iteration**: `claimBuilderFee` only iterates over `spotEngine.getProductIds()`. Perp product IDs are never iterated. Builder fees from perp trades — stored at `collectedBuilderFee[perpQuoteId][builderId]` — are never read and never cleared.

The credit step also uses `spotEngine.updateBalance(productId, sender, collectedFee)`, which would be incorrect for perp-derived fees even if the loop were extended, since perp builder fees are denominated in the quote asset and should be credited via the quote product ID. [4](#0-3) 

---

### Impact Explanation

Builder fees from every perp trade are permanently locked in the `OffchainExchange` contract. A registered builder with active fee rates on perp products will accumulate `collectedBuilderFee[perpQuoteId][builderId]` balances that can never be extracted. The asset delta is the total builder fee revenue from all perp volume, which in a live exchange is unbounded and grows monotonically. There is no admin escape hatch or sweep function for these balances.

---

### Likelihood Explanation

The trigger requires only: (a) a builder to be registered via `UpdateBuilder`, (b) a trader to submit an order with a non-zero `builderId` and `builderFeeRate` encoded in the `appendix`, and (c) that order to match against a perp product. All three are normal, supported protocol operations reachable by any unprivileged user. No special permissions or unusual conditions are required. [5](#0-4) 

---

### Recommendation

`claimBuilderFee` must be updated to:

1. Also iterate over `perpEngine.getProductIds()`.
2. For each product (spot or perp), look up `collectedBuilderFee[quoteIds[productId]][builderId]` rather than `collectedBuilderFee[productId][builderId]`, to match the write path in `applyFee`.
3. Credit the claimed amount to the builder via `spotEngine.updateBalance(quoteIds[productId], sender, collectedFee)` so the correct quote asset is credited.

Care must be taken to avoid double-counting when multiple products share the same `quoteId` — the loop should aggregate by `quoteId`, not by `productId`.

---

### Proof of Concept

1. Owner registers a builder with `builderId=1`, `lowestFeeRate=0`, `highestFeeRate=100bps`.
2. Trader submits a perp order with `appendix` encoding `builderId=1` and `builderFeeRate=10bps`.
3. The order matches. `applyFee` executes:
   - `market.quoteId` for the perp product = `quoteIds[perpProductId]` = e.g. `0`
   - `collectedBuilderFee[0][1] += builderFee` ← stored at key `0`
4. Builder calls `claimBuilderFee(builderSubaccount, 1)`.
5. Loop iterates `spotEngine.getProductIds()` = `[0, 2, 4, ...]` (spot IDs only).
6. For `productId=0`: reads `collectedBuilderFee[0][1]` — this happens to return the perp fee only if no spot fees also accumulated there, but the event emits `productId=0` regardless.
7. For perp product IDs (e.g. `1, 3, 5, ...`): never iterated. `collectedBuilderFee[quoteIds[perpId]][1]` is never read or zeroed.
8. If the perp quoteId differs from any spot productId in the loop, those fees are permanently locked. [6](#0-5) [7](#0-6)

### Citations

**File:** core/contracts/OffchainExchange.sol (L64-64)
```text
    mapping(uint32 => mapping(uint32 => int128)) internal collectedBuilderFee; // quoteId -> builder -> amount
```

**File:** core/contracts/OffchainExchange.sol (L384-391)
```text
    function _builderInfo(uint128 appendix)
        internal
        pure
        returns (uint32 builderId, int128 builderFeeRate)
    {
        builderId = uint32((appendix >> 48) & ((1 << 16) - 1));
        builderFeeRate = int128((appendix >> 38) & ((1 << 10) - 1)) * (10**13); // 0.1bps
    }
```

**File:** core/contracts/OffchainExchange.sol (L549-568)
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
        if (orderInfo.builderFee > 0) {
            collectedBuilderFee[market.quoteId][feeInfo.builderId] += orderInfo
                .builderFee;
```

**File:** core/contracts/OffchainExchange.sol (L869-889)
```text
    function claimBuilderFee(bytes32 sender, uint32 builderId)
        external
        onlyEndpoint
    {
        require(!RiskHelper.isIsolatedSubaccount(sender), ERR_UNAUTHORIZED);
        require(
            builders[builderId].owner == address(uint160(bytes20(sender))),
            ERR_UNAUTHORIZED
        );
        uint32[] memory productIds = spotEngine.getProductIds();
        for (uint32 i = 0; i < productIds.length; i++) {
            uint32 productId = productIds[i];
            int128 collectedFee = collectedBuilderFee[productId][builderId];
            if (collectedFee == 0) {
                continue;
            }
            emit ClaimBuilderFee(builderId, productId, sender, collectedFee);
            spotEngine.updateBalance(productId, sender, collectedFee);
            collectedBuilderFee[productId][builderId] = 0;
        }
    }
```
