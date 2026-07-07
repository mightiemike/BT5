### Title
Division by Zero in `PerpEngine.socializeSubaccount` When `openInterest` Is Zero Blocks Liquidation Finalization - (File: `core/contracts/PerpEngine.sol`)

---

### Summary

`PerpEngine.socializeSubaccount` divides by `state.openInterest` without a zero guard. When all other participants have closed their positions and the insolvent subaccount's own perp amount is already zero (a prerequisite for finalization), `openInterest` can be zero. The division reverts with `"DBZ"`, permanently blocking the finalization of insolvent subaccounts and preventing bad-debt recovery.

---

### Finding Description

Inside `PerpEngine.socializeSubaccount`, when the insurance fund cannot fully cover a subaccount's negative `vQuoteBalance`, the remaining loss is spread across all open-interest holders via:

```solidity
int128 fundingPerShare = -balance.vQuoteBalance.div(
    state.openInterest
);
```

`MathSD21x18.div` enforces `y != 0` with a hard `require`, so if `state.openInterest == 0` this call reverts unconditionally.

The function is reached from `ClearinghouseLiq._finalizeSubaccount`, which **requires** `balance.amount == 0` for every perp product before calling `perpEngine.socializeSubaccount`. Because the liquidatee's own position is already zero, it contributes nothing to `openInterest`. If every other participant has also closed their positions, `openInterest` is zero at the moment of the call.

The guard in `PerpEngineState.updateStates` (`if (state.openInterest == 0) { continue; }`) is unrelated — it only skips funding-rate updates and does not protect `socializeSubaccount`. [1](#0-0) 

The `div` helper that enforces the non-zero requirement: [2](#0-1) 

The prerequisite check in `_finalizeSubaccount` that zeroes the position before `socializeSubaccount` is called: [3](#0-2) 

The call site that triggers `socializeSubaccount`: [4](#0-3) 

---

### Impact Explanation

**Impact: High.**

When the revert fires, `liquidateSubaccountImpl` reverts entirely. The insolvent subaccount can never be finalized: its negative `vQuoteBalance` (bad debt) is permanently stuck, the insurance fund cannot be reconciled, and the protocol's accounting is corrupted. Any subsequent attempt to finalize the same subaccount will hit the same revert because the on-chain state is unchanged. [5](#0-4) 

---

### Likelihood Explanation

**Likelihood: Low.**

The trigger requires two conditions to coincide:

1. An insolvent subaccount whose `vQuoteBalance` loss exceeds the insurance fund.
2. All other participants in that perp market have closed their positions, leaving `openInterest == 0`.

This is plausible for low-activity or newly listed perp markets where a single large position dominates open interest and all counterparties exit before the insolvent account is finalized. [6](#0-5) 

---

### Recommendation

Add a zero-guard before the division in `socializeSubaccount`. If `openInterest` is zero there are no counterparties to absorb the loss; the remaining bad debt should be written off against the insurance fund or recorded as an unrecoverable deficit rather than attempting to spread it:

```solidity
if (balance.vQuoteBalance < 0) {
    if (state.openInterest == 0) {
        // No open interest to socialize against; absorb or record deficit
        balance.vQuoteBalance = 0;
    } else {
        int128 fundingPerShare = -balance.vQuoteBalance.div(
            state.openInterest
        );
        state.cumulativeFundingLongX18 += fundingPerShare;
        state.cumulativeFundingShortX18 -= fundingPerShare;
        balance.vQuoteBalance = 0;
    }
}
``` [7](#0-6) 

---

### Proof of Concept

**Setup:**

1. A perp market (e.g., `productId = 2`) has two participants:
   - **Alice**: long 10 units, `vQuoteBalance = -500` (deep loss).
   - **Bob**: short 10 units, `vQuoteBalance = +500`.

2. Bob closes his position → `openInterest` drops to `10` (only Alice's position remains).

3. Alice's position is liquidated in steps until `balance.amount == 0`, but `vQuoteBalance` remains `-500`. `openInterest` is now `0`.

4. Insurance fund holds only `100` (insufficient to cover `-500`).

**Trigger:**

```
liquidator calls Endpoint.liquidateSubaccount({
    liquidatee: alice,
    productId: type(uint32).max,   // triggers _finalizeSubaccount
    ...
})
```

**Execution path:**

```
liquidateSubaccountImpl
  └─ _finalizeSubaccount
       ├─ require(balance.amount == 0)  ✓ (Alice's amount is 0)
       └─ perpEngine.socializeSubaccount(alice, insurance=100)
            ├─ balance.vQuoteBalance = -500
            ├─ insuranceCover = min(100, 500) = 100
            ├─ balance.vQuoteBalance = -400  (still < 0)
            └─ fundingPerShare = 400 / state.openInterest
                                       ^^^^^^^^^^^^^^^^^^^
                                       openInterest == 0 → REVERT "DBZ"
```

The transaction reverts. Alice's subaccount can never be finalized. [8](#0-7)

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

**File:** core/contracts/libraries/MathSD21x18.sol (L62-68)
```text
    function div(int128 x, int128 y) internal pure returns (int128) {
        unchecked {
            require(y != 0, ERR_DIV_BY_ZERO);
            int256 result = (int256(x) * ONE_X18) / y;
            require(result >= MIN_X18 && result <= MAX_X18, ERR_OVERFLOW);
            return int128(result);
        }
```

**File:** core/contracts/ClearinghouseLiq.sol (L313-320)
```text
        for (uint32 i = 0; i < v.perpIds.length; ++i) {
            uint32 perpId = v.perpIds[i];
            IPerpEngine.Balance memory balance = perpEngine.getBalance(
                perpId,
                txn.liquidatee
            );
            require(balance.amount == 0, ERR_NOT_FINALIZABLE_SUBACCOUNT);
        }
```

**File:** core/contracts/ClearinghouseLiq.sol (L386-389)
```text
        v.insurance = perpEngine.socializeSubaccount(
            txn.liquidatee,
            v.insurance
        );
```

**File:** core/contracts/ClearinghouseLiq.sol (L598-647)
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

        if (
            (txn.amount < 0) &&
            (txn.isEncodedSpread ||
                address(productToEngine[txn.productId]) == address(spotEngine))
        ) {
            // when it's spread or spot liquidation, we need to make sure the liquidatee has
            // enough quote to buyback the liquidated amount.
            _assertCanLiquidateLiability(txn, spotEngine, perpEngine);
            _settlePositivePerpPnl(txn, spotEngine, perpEngine);
        }

        _assertLiquidationAmount(txn, spotEngine, perpEngine);

        // beyond this point, we can be sure that we can liquidate the entire
        // liquidation amount knowing that the insurance fund will remain solvent
        // subsequently we can just blast the remainder of the liquidation and
        // cover the quote balance from the insurance fund at the end
        _handleLiquidationPayment(txn, spotEngine, perpEngine);
    }
```

**File:** core/contracts/PerpEngineState.sol (L103-113)
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
```
