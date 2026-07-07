### Title
Division by Zero in `PerpEngine.socializeSubaccount` When `openInterest` Is Zero Permanently Blocks Liquidation Finalization — (`core/contracts/PerpEngine.sol`)

---

### Summary

`PerpEngine.socializeSubaccount` unconditionally divides by `state.openInterest` when spreading a liquidatee's residual negative `vQuoteBalance` across remaining participants. If `openInterest` is zero at the time of socialization — a reachable state when all other traders have closed their perp positions — the call reverts with a division-by-zero error. This permanently blocks the liquidation finalization path, leaving the liquidatee's bad debt unresolvable and the insurance funds that should cover it effectively locked in the contract.

---

### Finding Description

In `PerpEngine.socializeSubaccount`, after exhausting insurance coverage, the remaining negative `vQuoteBalance` is socialized by computing a per-share funding adjustment:

```solidity
int128 fundingPerShare = -balance.vQuoteBalance.div(
    state.openInterest
);
``` [1](#0-0) 

`MathSD21x18.div` enforces `require(y != 0, "DBZ")`, so if `state.openInterest == 0` the entire call reverts. [2](#0-1) 

`socializeSubaccount` is invoked from `ClearinghouseLiq._finalizeSubaccount` after all perp position sizes have been verified to be zero:

```solidity
v.insurance = perpEngine.socializeSubaccount(
    txn.liquidatee,
    v.insurance
);
``` [3](#0-2) 

The pre-condition `require(balance.amount == 0, ERR_NOT_FINALIZABLE_SUBACCOUNT)` ensures the liquidatee's own position is already closed before socialization is attempted: [4](#0-3) 

Because the liquidatee's position is already zero, their contribution to `openInterest` is already removed. If every other trader has also closed their perp positions for that product, `state.openInterest == 0`. The liquidatee can still carry a negative `vQuoteBalance` (accumulated unrealized loss from prior trading that was never settled), so the branch `if (balance.vQuoteBalance < 0)` is entered and the zero-denominator division is hit.

`PerpEngineState.updateStates` already demonstrates awareness that `openInterest` can be zero and guards against it with an explicit `continue`: [5](#0-4) 

No equivalent guard exists in `socializeSubaccount`.

---

### Impact Explanation

When the revert fires:

1. `_finalizeSubaccount` reverts, causing the entire `liquidateSubaccountImpl` transaction to revert.
2. The liquidatee's subaccount cannot be finalized — its negative `vQuoteBalance` (bad debt) is permanently unresolvable.
3. The insurance funds allocated to cover that bad debt (`v.insurance`) are consumed by the revert and never applied; they remain locked in the contract with no code path to recover them.
4. The protocol's bad-debt accounting is permanently corrupted for that product: the negative `vQuoteBalance` persists on-chain but can never be cleared.

This matches the M-02 class: a denominator that legitimately reaches zero causes an accounting function to fail, locking funds and leaving protocol state irrecoverable.

---

### Likelihood Explanation

The trigger requires two concurrent conditions:

- **All other traders close their perp positions** for the affected product (`openInterest → 0`). This is a normal market event (e.g., low-liquidity product, end-of-day close-out, or a product nearing deprecation).
- **The liquidatee has a residual negative `vQuoteBalance`** with zero position size. This occurs whenever a trader closes a losing position without settling PnL, which is a standard protocol flow.

Neither condition requires privileged access, governance capture, or external oracle manipulation. Any unprivileged liquidator calling `liquidateSubaccountImpl` with `productId == type(uint32).max` (the finalization sentinel) triggers the path. [6](#0-5) 

---

### Recommendation

Mirror the guard already present in `PerpEngineState.updateStates`: skip socialization (or handle it as a no-op) when `state.openInterest == 0`. If there are no other open-interest holders to absorb the loss, the entire residual should be charged to the insurance fund or written off directly:

```solidity
if (balance.vQuoteBalance < 0) {
    int128 insuranceCover = MathHelper.min(insurance, -balance.vQuoteBalance);
    insurance -= insuranceCover;
    balance.vQuoteBalance += insuranceCover;
    state.availableSettle += insuranceCover;

    if (balance.vQuoteBalance < 0) {
+       if (state.openInterest == 0) {
+           // No other participants; write off residual against insurance or absorb
+           balance.vQuoteBalance = 0;
+       } else {
            int128 fundingPerShare = -balance.vQuoteBalance.div(state.openInterest);
            state.cumulativeFundingLongX18 += fundingPerShare;
            state.cumulativeFundingShortX18 -= fundingPerShare;
            balance.vQuoteBalance = 0;
+       }
    }
}
```

---

### Proof of Concept

1. Deploy the protocol with one perp product.
2. Trader A opens a long position; Trader B opens a short position.
3. Price moves against Trader A; Trader A closes their position at a loss (position size → 0, `vQuoteBalance < 0`).
4. Trader B closes their position (position size → 0). Now `state.openInterest == 0`.
5. Trader A's quote balance drops below maintenance margin; a liquidator calls `liquidateSubaccountImpl` with `productId == type(uint32).max`.
6. `_finalizeSubaccount` passes all pre-checks (all perp `balance.amount == 0`), then calls `perpEngine.socializeSubaccount`.
7. Inside `socializeSubaccount`, `balance.vQuoteBalance < 0` is true and `state.openInterest == 0`; `MathSD21x18.div` reverts with `"DBZ"`.
8. The liquidation transaction reverts. Trader A's bad debt is permanently stuck; insurance funds are inaccessible. [7](#0-6)

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

**File:** core/contracts/libraries/MathSD21x18.sol (L62-66)
```text
    function div(int128 x, int128 y) internal pure returns (int128) {
        unchecked {
            require(y != 0, ERR_DIV_BY_ZERO);
            int256 result = (int256(x) * ONE_X18) / y;
            require(result >= MIN_X18 && result <= MAX_X18, ERR_OVERFLOW);
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

**File:** core/contracts/PerpEngineState.sol (L111-113)
```text
            if (state.openInterest == 0) {
                continue;
            }
```
