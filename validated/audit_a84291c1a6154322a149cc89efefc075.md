### Title
Inequitable Insurance Distribution in `socializeSubaccount` Due to Shared Mutable Counter Depletion During Iteration — (`File: core/contracts/PerpEngine.sol`)

### Summary
`PerpEngine.socializeSubaccount` iterates over all perp products and applies insurance coverage to each product's negative `vQuoteBalance`. The shared `insurance` counter is decremented inside the loop after each product is covered. This causes later products in the `productIds` array to receive less (or zero) insurance coverage than earlier ones, even when the total insurance would have been sufficient to partially cover all products proportionally. Products that receive no insurance are fully socialized, spreading losses to all other traders holding positions in those products.

### Finding Description

`PerpEngine.socializeSubaccount` is called from `ClearinghouseLiq._finalizeSubaccount` during liquidation finalization: [1](#0-0) 

The loop iterates over all perp product IDs and for each product with a negative `vQuoteBalance`, computes:

```solidity
int128 insuranceCover = MathHelper.min(insurance, -balance.vQuoteBalance);
insurance -= insuranceCover;
``` [2](#0-1) 

The `insurance` variable is decremented after each product is processed. This means the available insurance for product `i+1` is `insurance - sum(insuranceCover[0..i])`. If insurance is insufficient to cover all products, earlier products in the `productIds` array receive full coverage while later products receive partial or zero coverage and are fully socialized via `cumulativeFundingLongX18`/`cumulativeFundingShortX18` adjustments: [3](#0-2) 

The caller in `_finalizeSubaccount` passes the global `insurance` storage variable: [4](#0-3) 

### Impact Explanation

When a subaccount is finalized during liquidation and the insurance fund is insufficient to cover all negative `vQuoteBalance` positions across multiple perp products, the distribution is inequitable:

- **Products early in `productIds`**: receive full insurance coverage.
- **Products late in `productIds`**: receive zero coverage and are fully socialized.

Full socialization adjusts `cumulativeFundingLongX18` and `cumulativeFundingShortX18` for the affected product, directly reducing the realized PnL of all other traders holding positions in that product. These traders suffer losses that would have been partially absorbed by the insurance fund under a proportional distribution. The corrupted state is `state.cumulativeFundingLongX18` and `state.cumulativeFundingShortX18` for later-ordered perp products.

### Likelihood Explanation

This triggers whenever a subaccount is finalized during liquidation (`txn.productId == type(uint32).max`) and the subaccount holds negative `vQuoteBalance` across two or more perp products with insufficient insurance to cover all of them. Any unprivileged liquidator can trigger this path by calling `liquidateSubaccount` with `productId = type(uint32).max`. [5](#0-4) 

The product ordering is fixed by `getProductIds()` (insertion order), so the inequity is deterministic and repeatable.

### Recommendation

Compute the total insurance needed across all products first, then distribute proportionally:

```solidity
// Pass 1: compute total deficit
int128 totalDeficit = 0;
for (uint128 i = 0; i < _productIds.length; ++i) {
    (, Balance memory balance) = getStateAndBalance(_productIds[i], subaccount);
    if (balance.vQuoteBalance < 0) {
        totalDeficit += -balance.vQuoteBalance;
    }
}
// Pass 2: distribute proportionally
for (uint128 i = 0; i < _productIds.length; ++i) {
    ...
    int128 insuranceCover = MathHelper.min(
        insurance.mul(-balance.vQuoteBalance).div(totalDeficit),
        -balance.vQuoteBalance
    );
    ...
}
```

### Proof of Concept

**Setup**: Two perp products, Product A (added first) and Product B (added second). Subaccount being finalized has:
- Product A: `vQuoteBalance = -100`
- Product B: `vQuoteBalance = -100`
- `insurance = 150`

**Current behavior**:
- Product A: `insuranceCover = min(150, 100) = 100`, `insurance = 50`, fully covered.
- Product B: `insuranceCover = min(50, 100) = 50`, `insurance = 0`, 50 remaining is socialized → `cumulativeFundingLongX18` for Product B is adjusted, harming all other Product B holders.

**Expected proportional behavior**:
- Product A: `insuranceCover = 75`, 25 socialized.
- Product B: `insuranceCover = 75`, 25 socialized.

Product B holders bear 2× the socialization loss they should under the current greedy ordering, while Product A holders bear none.

### Citations

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

**File:** core/contracts/ClearinghouseLiq.sol (L386-389)
```text
        v.insurance = perpEngine.socializeSubaccount(
            txn.liquidatee,
            v.insurance
        );
```

**File:** core/contracts/ClearinghouseLiq.sol (L598-627)
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
        require(
            txn.productId != QUOTE_PRODUCT_ID,
            ERR_INVALID_LIQUIDATION_PARAMS
        );

        ISpotEngine spotEngine = ISpotEngine(
            address(engineByType[IProductEngine.EngineType.SPOT])
        );
        IPerpEngine perpEngine = IPerpEngine(
            address(engineByType[IProductEngine.EngineType.PERP])
        );

        if (_finalizeSubaccount(txn, spotEngine, perpEngine)) {
            if (RiskHelper.isIsolatedSubaccount(txn.liquidatee)) {
                IOffchainExchange(
                    IEndpoint(getEndpoint()).getOffchainExchange()
                ).tryCloseIsolatedSubaccount(txn.liquidatee);
            }
            return;
        }
```
