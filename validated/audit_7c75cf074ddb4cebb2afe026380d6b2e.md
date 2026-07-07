### Title
Post-Trade Health Check is a No-Op — Trades at Unfavorable Prices Can Cause Socialized Losses - (File: `core/contracts/OffchainExchange.sol`)

---

### Summary

`OffchainExchange.isHealthy` is a stub that unconditionally returns `true`. The post-trade health assertions in `matchOrders` are therefore no-ops. A user who signs an order at a deeply unfavorable price (e.g., a long position selling far below oracle) can have that order matched on-chain with no on-chain rejection, leaving a negative `vQuoteBalance` that is later socialized across all perp participants.

---

### Finding Description

In `matchOrders`, after both sides' balances are updated, the contract asserts:

```solidity
require(isHealthy(taker.order.sender), ERR_INVALID_TAKER);
require(isHealthy(maker.order.sender), ERR_INVALID_MAKER);
``` [1](#0-0) 

However, `isHealthy` is defined as:

```solidity
function isHealthy(
    bytes32 /* subaccount */
) internal view virtual returns (bool) {
    return true;
}
``` [2](#0-1) 

The function is `virtual` but no contract in the production scope overrides it. The result is that **every post-trade health check unconditionally passes**, regardless of the resulting position health.

There is no oracle-price bound on the execution price either. The only price constraint in `matchOrders` is that orders must be crossing (bid ≥ ask):

```solidity
if (maker.order.amount > 0) {
    require(maker.order.priceX18 >= taker.order.priceX18, ERR_ORDERS_CANNOT_BE_MATCHED);
} else {
    require(maker.order.priceX18 <= taker.order.priceX18, ERR_ORDERS_CANNOT_BE_MATCHED);
}
``` [3](#0-2) 

Execution happens at the maker's price with no floor or ceiling relative to the oracle:

```solidity
ordersInfo.maker.quoteDelta = ordersInfo.taker.amountDelta.mul(maker.order.priceX18);
``` [4](#0-3) 

When a long position is partially closed at a price far below the oracle, the resulting `vQuoteBalance` for the remaining position becomes deeply negative. When that position is eventually liquidated, `socializeSubaccount` in `PerpEngine` distributes the shortfall across all open-interest holders:

```solidity
int128 fundingPerShare = -balance.vQuoteBalance.div(state.openInterest);
state.cumulativeFundingLongX18 += fundingPerShare;
state.cumulativeFundingShortX18 -= fundingPerShare;
``` [5](#0-4) 

---

### Impact Explanation

Any trade that leaves a participant with negative initial health passes silently. A user who deliberately sells a long perp position at a price far below oracle (or buys a short position far above oracle) incurs a realized loss that is embedded in `vQuoteBalance`. Upon liquidation, this loss is socialized to all other perp participants via the cumulative funding mechanism. The insurance fund is consumed first, but if it is insufficient, the loss is mutualized. This is a direct, quantifiable asset loss for innocent third-party users.

---

### Likelihood Explanation

The attack requires the sequencer to submit a `matchOrders` transaction pairing the attacker's unfavorable maker order with a taker order the attacker controls from a second address. Because the sequencer is a centralized component and the on-chain code provides no price-bound or health-bound rejection, a compromised or malicious sequencer can trivially execute this. Additionally, if the protocol exposes a slow-mode path (common in sequencer-based DEXs) that allows direct `Endpoint` submission, the attacker can bypass the sequencer entirely. The on-chain code is the last line of defense and it is absent.

---

### Recommendation

1. **Override `isHealthy` with a real health check.** The `virtual` pattern exists precisely for this purpose. The override should call `clearinghouse.getHealth(subaccount, IProductEngine.HealthType.INITIAL) >= 0`.

2. **Add an oracle-price bound on execution price.** When a perp position is being reduced (same-sign position and opposite-sign order), require that the execution price does not deviate from the oracle price by more than a configurable threshold (e.g., the maintenance margin fraction). This mirrors the Deriverse mitigation of bounding order prices relative to the critical/liquidation price.

3. **Reject trades that produce negative PnL for the position-reducing side when the account is not in margin call.** Analogous to the Deriverse fix: if `loss > 0 && !marginCall`, revert.

---

### Proof of Concept

1. Attacker holds 1 BTC long perp, entry price $90,000, oracle price $90,100. Initial health is positive.
2. Attacker signs an ask order for 0.5 BTC at `priceX18 = $50,000e18` from address A.
3. Attacker signs a bid order for 0.5 BTC at `priceX18 = $50,000e18` from address B.
4. Sequencer (or attacker via slow mode) calls `Endpoint → matchOrders`.
5. `_updateBalances` credits address A with `0.5 × $50,000 = $25,000` quote and removes 0.5 BTC. At oracle price the fair value was `$45,050`. The `vQuoteBalance` for the remaining 0.5 BTC position is now `−$20,050` below fair value.
6. `require(isHealthy(A))` passes unconditionally.
7. Address A's remaining 0.5 BTC position is now near or below maintenance margin. When liquidated, `socializeSubaccount` distributes the `$20,050` shortfall across all perp open-interest holders via `cumulativeFundingLongX18` / `cumulativeFundingShortX18`. [6](#0-5) [7](#0-6)

### Citations

**File:** core/contracts/OffchainExchange.sol (L625-629)
```text
    function isHealthy(
        bytes32 /* subaccount */
    ) internal view virtual returns (bool) {
        return true;
    }
```

**File:** core/contracts/OffchainExchange.sol (L732-742)
```text
        if (maker.order.amount > 0) {
            require(
                maker.order.priceX18 >= taker.order.priceX18,
                ERR_ORDERS_CANNOT_BE_MATCHED
            );
        } else {
            require(
                maker.order.priceX18 <= taker.order.priceX18,
                ERR_ORDERS_CANNOT_BE_MATCHED
            );
        }
```

**File:** core/contracts/OffchainExchange.sol (L760-762)
```text
        ordersInfo.maker.quoteDelta = ordersInfo.taker.amountDelta.mul(
            maker.order.priceX18
        );
```

**File:** core/contracts/OffchainExchange.sol (L811-827)
```text
        _updateBalances(
            callState,
            market.quoteId,
            taker.order.sender,
            ordersInfo.taker.amountDelta,
            ordersInfo.taker.quoteDelta
        );
        _updateBalances(
            callState,
            market.quoteId,
            maker.order.sender,
            ordersInfo.maker.amountDelta,
            ordersInfo.maker.quoteDelta
        );

        require(isHealthy(taker.order.sender), ERR_INVALID_TAKER);
        require(isHealthy(maker.order.sender), ERR_INVALID_MAKER);
```

**File:** core/contracts/PerpEngine.sol (L141-178)
```text
    function socializeSubaccount(bytes32 subaccount, int128 insurance)
        external
        returns (int128)
    {
        require(msg.sender == address(_clearinghouse), ERR_UNAUTHORIZED);

        uint32[] memory _productIds = getProductIds();
        for (uint128 i = 0; i < _productIds.length; ++i) {
            uint32 productId = _productIds[i];
            (State memory state, Balance memory balance) = getStateAndBalance(
                productId,
                subaccount
            );
            if (balance.vQuoteBalance < 0) {
                int128 insuranceCover = MathHelper.min(
                    insurance,
                    -balance.vQuoteBalance
                );
                insurance -= insuranceCover;
                balance.vQuoteBalance += insuranceCover;
                state.availableSettle += insuranceCover;

                // actually socialize if still not enough
                if (balance.vQuoteBalance < 0) {
                    // socialize across all other participants
                    int128 fundingPerShare = -balance.vQuoteBalance.div(
                        state.openInterest
                    );
                    state.cumulativeFundingLongX18 += fundingPerShare;
                    state.cumulativeFundingShortX18 -= fundingPerShare;
                    balance.vQuoteBalance = 0;
                }
                _setState(productId, state);
                _setBalanceAndUpdateBitmap(productId, subaccount, balance);
            }
        }
        return insurance;
    }
```
