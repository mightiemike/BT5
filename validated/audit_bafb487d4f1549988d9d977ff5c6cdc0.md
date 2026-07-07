### Title
Dust Spot Deposit Blocks Subaccount Finalization and Socialization — (`File: core/contracts/ClearinghouseLiq.sol`)

---

### Summary

`_finalizeSubaccount` in `ClearinghouseLiq.sol` gates socialization on all spot asset balances being `<= 0`. Because `depositCollateral` in `Clearinghouse.sol` enforces no minimum deposit and no post-deposit health check, an attacker can deposit 1 wei of any eligible spot asset into an underwater subaccount at any time, causing every finalization attempt to revert with `ERR_NOT_FINALIZABLE_SUBACCOUNT`. The attack is repeatable, cheap, and delays socialization indefinitely while the negative quote balance continues to accrue borrow interest.

---

### Finding Description

`_finalizeSubaccount` is the only path to socialize an insolvent subaccount in Nado. It is triggered by a liquidator submitting `txn.productId == type(uint32).max`. Before socialization can proceed, the function iterates over every registered spot product and enforces:

```solidity
// ClearinghouseLiq.sol lines 301–311
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
```

Any positive balance on any risk-weighted spot product causes an unconditional revert. [1](#0-0) 

A second, stricter gate applies when `canLiquidateMore` is true (insurance can cover the residual debt):

```solidity
// ClearinghouseLiq.sol lines 372–383
if (v.canLiquidateMore) {
    for (uint32 i = 1; i < v.spotIds.length; ++i) {
        ...
        require(balance.amount == 0, ERR_NOT_FINALIZABLE_SUBACCOUNT);
    }
}
```

Here even a negative dust balance blocks finalization. [2](#0-1) 

The deposit entry point that enables the attack is `depositCollateral` in `Clearinghouse.sol`:

```solidity
// Clearinghouse.sol lines 193–209
function depositCollateral(IEndpoint.DepositCollateral calldata txn)
    external
    virtual
    onlyEndpoint
{
    require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);
    require(txn.amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
    ...
    spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
    emit ModifyCollateral(amountRealized, txn.sender, txn.productId);
}
```

There is no minimum deposit amount check and no post-deposit health check. A deposit of 1 wei is accepted unconditionally for any non-isolated subaccount, regardless of whether that subaccount is already under maintenance health. [3](#0-2) 

`checkMinDeposit` exists in `Clearinghouse.sol` (lines 698–715) but is an `external` view function that returns a `bool` — it is not called inside `depositCollateral` and therefore provides no on-chain enforcement. [4](#0-3) 

The same dust-blocking pattern also applies to `_assertCanLiquidateLiability`, which enforces `require(balance.amount <= 0, ERR_NOT_LIQUIDATABLE_LIABILITIES)` for every risk-weighted spot product before allowing liability liquidation, creating a second surface for the same attack. [5](#0-4) 

---

### Impact Explanation

Socialization (`spotEngine.socializeSubaccount` / `perpEngine.socializeSubaccount`) is permanently deferred as long as the attacker keeps a dust positive spot balance on the liquidatee. During this window the negative quote balance continues to compound borrow interest through `_updateState` in `SpotEngineState.sol`, growing the bad debt that will eventually be mutualized across all depositors. [6](#0-5) 

The socialization path that is blocked:

```solidity
// ClearinghouseLiq.sol lines 386–411
v.insurance = perpEngine.socializeSubaccount(txn.liquidatee, v.insurance);
...
if (v.insurance <= 0) {
    spotEngine.socializeSubaccount(txn.liquidatee);
}
``` [7](#0-6) 

The concrete corrupted state is: the negative `amountNormalized` in `balances[QUOTE_PRODUCT_ID][liquidatee]` is never zeroed out via `socializeSubaccount`, so `totalDepositsNormalized` is never reduced to absorb the loss, and the bad debt silently inflates the borrow-side multiplier for all depositors. [8](#0-7) 

---

### Likelihood Explanation

The attack requires only that the attacker submit a signed `DepositCollateral` transaction to the sequencer for the targeted subaccount (which they control). The cost is the gas for one deposit plus 1 wei of token. The attack can be repeated after each liquidator attempt to clear the dust position. Because `depositCollateral` imposes no health check, the deposit is always accepted on-chain even when the subaccount is deeply insolvent. The only off-chain mitigation would be sequencer-level enforcement of `checkMinDeposit`, but this is not guaranteed by the on-chain protocol and can be circumvented if the sequencer is permissive or if slow-mode submission paths exist.

---

### Recommendation

1. **Short term**: Enforce a minimum deposit threshold inside `depositCollateral` on-chain by calling `checkMinDeposit` and reverting if the deposit value is below the protocol dust threshold.
2. **Short term**: In `_finalizeSubaccount` and `_assertCanLiquidateLiability`, skip spot products whose balance is below a configurable dust threshold rather than treating any positive amount as a blocking condition.
3. **Long term**: Redesign the finalization gate so that economically insignificant balances (below a minimum notional value) are treated as zero for the purpose of the `ERR_NOT_FINALIZABLE_SUBACCOUNT` check.

---

### Proof of Concept

1. Alice controls subaccount `A` which holds a large USDC borrow and no meaningful collateral. Maintenance health is negative; `A` is liquidatable.
2. Liquidators clear all of Alice's positive spot positions on `A`.
3. A liquidator submits `LiquidateSubaccount` with `productId = type(uint32).max` to trigger `_finalizeSubaccount`.
4. Before (or between) sequencer batches, Alice submits `DepositCollateral` for `A` with `productId = WETH_PRODUCT_ID` and `amount = 1`. `depositCollateral` accepts it unconditionally.
5. `_finalizeSubaccount` reaches line 310, reads `balance.amount = 1 > 0` for WETH, and reverts with `ERR_NOT_FINALIZABLE_SUBACCOUNT`.
6. The liquidator must now submit a separate `LiquidateSubaccount` for WETH on `A`. Alice immediately re-deposits 1 wei of WETH (or any other eligible spot product) after each clearance.
7. Socialization never completes; the negative USDC balance on `A` accrues borrow interest indefinitely via `_updateState`, growing the eventual loss mutualized across all depositors.

### Citations

**File:** core/contracts/ClearinghouseLiq.sol (L235-244)
```text
        for (uint32 i = 1; i < spotIds.length; ++i) {
            uint32 spotId = spotIds[i];
            if (spotEngine.getRisk(spotId).longWeightInitialX18 == 0) {
                continue;
            }
            ISpotEngine.Balance memory balance = spotEngine.getBalance(
                spotId,
                txn.liquidatee
            );
            require(balance.amount <= 0, ERR_NOT_LIQUIDATABLE_LIABILITIES);
```

**File:** core/contracts/ClearinghouseLiq.sol (L301-311)
```text
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
```

**File:** core/contracts/ClearinghouseLiq.sol (L372-383)
```text
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
```

**File:** core/contracts/ClearinghouseLiq.sol (L386-411)
```text
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
```

**File:** core/contracts/Clearinghouse.sol (L193-209)
```text
    function depositCollateral(IEndpoint.DepositCollateral calldata txn)
        external
        virtual
        onlyEndpoint
    {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);
        require(txn.amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        ISpotEngine spotEngine = _spotEngine();
        uint8 decimals = _decimals(txn.productId);

        require(decimals <= MAX_DECIMALS);
        int256 multiplier = int256(10**(MAX_DECIMALS - decimals));
        int128 amountRealized = int128(txn.amount) * int128(multiplier);

        spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
        emit ModifyCollateral(amountRealized, txn.sender, txn.productId);
    }
```

**File:** core/contracts/Clearinghouse.sol (L698-715)
```text
    function checkMinDeposit(
        uint32 productId,
        uint128 amount,
        int256 minDepositAmount
    ) external returns (bool) {
        require(amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        uint8 decimals = _decimals(productId);
        require(decimals <= MAX_DECIMALS);

        int256 multiplier = int256(10**(MAX_DECIMALS - decimals));
        int128 amountRealized = int128(multiplier) * int128(amount);
        int128 priceX18 = ONE;
        if (productId != QUOTE_PRODUCT_ID) {
            priceX18 = _getPriceX18(productId);
        }

        return priceX18.mul(amountRealized) >= minDepositAmount;
    }
```

**File:** core/contracts/SpotEngineState.sol (L52-100)
```text
    function _updateState(
        uint32 productId,
        State memory state,
        uint128 dt
    ) internal {
        int128 borrowRateMultiplierX18;
        int128 totalDeposits = state.totalDepositsNormalized.mul(
            state.cumulativeDepositsMultiplierX18
        );
        int128 totalBorrows = state.totalBorrowsNormalized.mul(
            state.cumulativeBorrowsMultiplierX18
        );
        int128 utilizationRatioX18 = totalBorrows.div(totalDeposits);
        int128 minDepositRateX18;
        {
            Config memory config = configs[productId];

            // annualized borrower rate
            int128 borrowerRateX18 = config.interestFloorX18;
            if (utilizationRatioX18 == 0) {
                // setting borrowerRateX18 to 0 here has the property that
                // adding a product at the beginning of time and not using it until time T
                // results in the same state as adding the product at time T
                borrowerRateX18 = 0;
            } else if (utilizationRatioX18 < config.interestInflectionUtilX18) {
                borrowerRateX18 += config
                    .interestSmallCapX18
                    .mul(utilizationRatioX18)
                    .div(config.interestInflectionUtilX18);
            } else {
                borrowerRateX18 +=
                    config.interestSmallCapX18 +
                    config.interestLargeCapX18.mul(
                        (
                            (utilizationRatioX18 -
                                config.interestInflectionUtilX18).div(
                                    ONE - config.interestInflectionUtilX18
                                )
                        )
                    );
            }

            // convert to per second
            borrowerRateX18 = borrowerRateX18.div(
                MathSD21x18.fromInt(31536000)
            );
            borrowRateMultiplierX18 = (ONE + borrowerRateX18).pow(int128(dt));
            minDepositRateX18 = config.minDepositRateX18;
        }
```

**File:** core/contracts/SpotEngine.sol (L243-277)
```text
    function socializeSubaccount(bytes32 subaccount) external {
        require(msg.sender == address(_clearinghouse), ERR_UNAUTHORIZED);

        uint32[] memory _productIds = getProductIds();
        for (uint128 i = 0; i < _productIds.length; ++i) {
            uint32 productId = _productIds[i];

            State memory state = states[productId];
            Balance memory balance = balanceNormalizedToBalance(
                state,
                balances[productId][subaccount]
            );
            if (balance.amount < 0) {
                int128 totalDeposited = state.totalDepositsNormalized.mul(
                    state.cumulativeDepositsMultiplierX18
                );

                state.cumulativeDepositsMultiplierX18 = (totalDeposited +
                    balance.amount).div(state.totalDepositsNormalized);

                require(state.cumulativeDepositsMultiplierX18 > 0);

                state.totalBorrowsNormalized += balance.amount.div(
                    state.cumulativeBorrowsMultiplierX18
                );

                _setBalanceAndUpdateBitmap(
                    productId,
                    subaccount,
                    BalanceNormalized({amountNormalized: 0})
                );
                _setState(productId, state);
            }
        }
    }
```
