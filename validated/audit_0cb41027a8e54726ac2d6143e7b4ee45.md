The critical code paths are now fully traced. Let me confirm the `_updateBalance` behavior and the `availableSettle` accounting gap.

### Title
`_finalizeSubaccount` Bypasses `availableSettle` Cap and Fails to Decrement It, Enabling Unbacked USDC Credit and Double-Settlement — (`core/contracts/ClearinghouseLiq.sol`)

---

### Summary

`_finalizeSubaccount` settles the full raw `balance.vQuoteBalance` of every perp product via `_settlePnlAgainstLiquidator` without consulting or decrementing `state.availableSettle`. The normal settlement path (`PerpEngine.settlePnl`) explicitly caps settlement at `min(positionPnl, state.availableSettle)` and decrements the pool. The finalization path does neither, breaking the protocol's core settlement accounting invariant.

---

### Finding Description

**Normal settlement path** (`PerpEngine.settlePnl`, lines 88–100):

```solidity
(int128 canSettle, State memory state, Balance memory balance)
    = getSettlementState(productId, subaccount);   // min(positionPnl, state.availableSettle)
state.availableSettle -= canSettle;                // pool decremented
balance.vQuoteBalance -= canSettle;
```

`getSettlementState` computes `availableSettle = min(calculatePositionPnl(balance, productId), state.availableSettle)`, capping what can be settled at the pool's capacity.

**Finalization path** (`_finalizeSubaccount`, lines 322–337):

```solidity
if (balance.vQuoteBalance > 0) {
    _settlePnlAgainstLiquidator(txn, perpId, balance.vQuoteBalance, ...);
}
```

`_settlePnlAgainstLiquidator` (lines 266–269) calls:
```solidity
perpEngine.updateBalance(perpId, txn.liquidatee, 0, -pnl);   // vQuoteBalance zeroed
perpEngine.updateBalance(perpId, txn.sender,    0,  pnl);
spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.liquidatee,  pnl);  // USDC credited
spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender,     -pnl);  // liquidator pays
```

`perpEngine.updateBalance` calls `_updateBalance` (`PerpEngineState.sol`, lines 23–52), which only touches `state.openInterest` and `balance.vQuoteBalance`. **`state.availableSettle` is never read or modified.** There is no `min(vQuoteBalance, state.availableSettle)` guard anywhere in this path.

Two invariants are simultaneously broken:

1. **No cap**: the liquidatee receives USDC credit equal to the full `balance.vQuoteBalance`, even when `balance.vQuoteBalance > state.availableSettle`.
2. **No decrement**: `state.availableSettle` is left unchanged after the settlement, so other users can still call `settlePnl` and drain the same pool.

---

### Impact Explanation

**Unbacked USDC credit**: When `balance.vQuoteBalance > state.availableSettle` for any perp product, the liquidatee is credited with more USDC than the perp engine's pool contains. The spot engine's USDC accounting goes negative relative to actual backing.

**Double-settlement**: Because `state.availableSettle` is not decremented, any other user with positive PnL on the same product can subsequently call `settlePnl` and receive USDC from the same pool that was already implicitly consumed during finalization. The same `availableSettle` pool is effectively settled twice.

**Liquidator forced to absorb excess**: The liquidator's spot USDC balance is debited by the full `pnl` (line 269). If `pnl > state.availableSettle`, the liquidator is forced to pay more than the protocol's pool should allow, with no recourse.

Across N perp products, the total unbacked USDC created equals `sum(max(0, vQuoteBalance_i - availableSettle_i))` for all products where the liquidatee's `vQuoteBalance` exceeds the per-product pool.

---

### Likelihood Explanation

The preconditions are reachable in normal protocol operation:

- A subaccount accumulates positive `vQuoteBalance` on multiple perp products by closing profitable positions (amount becomes 0, vQuoteBalance remains positive).
- The subaccount's overall health falls below maintenance (e.g., due to a large spot liability or negative PnL on another product), making it liquidatable.
- `state.availableSettle` for those products is low (common when few traders have settled negative PnL into the pool, or when the pool has already been partially drained).
- A liquidator calls `liquidateSubaccountImpl(productId=uint32.max)` to finalize.

No privileged access, oracle manipulation, or reentrancy is required. The path is a standard public liquidation call.

---

### Recommendation

In `_finalizeSubaccount`, replace the direct `balance.vQuoteBalance` read with a call to `getSettlementState` (which applies the `availableSettle` cap), and ensure `state.availableSettle` is decremented after settlement — mirroring exactly what `PerpEngine.settlePnl` does:

```solidity
(int128 canSettle, , ) = perpEngine.getSettlementState(perpId, txn.liquidatee);
if (canSettle > 0) {
    _settlePnlAgainstLiquidator(txn, perpId, canSettle, spotEngine, perpEngine);
    // also decrement state.availableSettle by canSettle
}
```

Alternatively, route finalization settlement through `perpEngine.settlePnl` (which already handles the cap and decrement) and only transfer the net settled amount to/from the liquidator.

---

### Proof of Concept

```
Setup:
  - Product perpId=2, state.availableSettle = 50 USDC
  - Liquidatee has: balance.amount = 0, balance.vQuoteBalance = 200 USDC (on perpId=2)
  - Liquidatee has: spot USDC balance = -300 USDC (making it under maintenance)
  - Liquidator has: spot USDC balance = 500 USDC

Step 1: Call liquidateSubaccountImpl(productId=uint32.max)
  → _finalizeSubaccount triggers
  → balance.amount == 0 check passes
  → balance.vQuoteBalance = 200 > 0, so _settlePnlAgainstLiquidator(perpId=2, pnl=200) is called

Step 2: Inside _settlePnlAgainstLiquidator:
  → perpEngine.updateBalance(2, liquidatee, 0, -200): vQuoteBalance → 0, availableSettle unchanged (still 50)
  → spotEngine.updateBalance(QUOTE, liquidatee, +200): liquidatee USDC → -100
  → spotEngine.updateBalance(QUOTE, liquidator, -200): liquidator USDC → 300

Step 3: State after finalization:
  → state.availableSettle for perpId=2 = 50 (NOT decremented)
  → Liquidatee received 200 USDC credit; only 50 was available in the pool → 150 USDC unbacked

Step 4: Another user with vQuoteBalance=50 on perpId=2 calls settlePnl:
  → getSettlementState returns canSettle = min(50, 50) = 50
  → state.availableSettle decremented to 0
  → That user receives 50 USDC — the same 50 that was already implicitly consumed in Step 2

Assert: total USDC credited from perpId=2 pool = 200 + 50 = 250 > state.availableSettle (50)
```

The invariant `sum(settled) <= state.availableSettle` is violated. The protocol has issued 250 USDC of settlement credit against a 50 USDC pool.

---

**Root cause references:**

- `_finalizeSubaccount` positive PnL loop (no `availableSettle` cap): [1](#0-0) 
- `_settlePnlAgainstLiquidator` (no `availableSettle` read or decrement): [2](#0-1) 
- `_updateBalance` (never touches `state.availableSettle`): [3](#0-2) 
- Normal `settlePnl` path (correct: caps and decrements `availableSettle`): [4](#0-3) 
- `getSettlementState` cap logic: [5](#0-4)

### Citations

**File:** core/contracts/ClearinghouseLiq.sol (L259-270)
```text
    function _settlePnlAgainstLiquidator(
        IEndpoint.LiquidateSubaccount calldata txn,
        uint32 perpId,
        int128 pnl,
        ISpotEngine spotEngine,
        IPerpEngine perpEngine
    ) internal {
        perpEngine.updateBalance(perpId, txn.liquidatee, 0, -pnl);
        perpEngine.updateBalance(perpId, txn.sender, 0, pnl);
        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.liquidatee, pnl);
        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -pnl);
    }
```

**File:** core/contracts/ClearinghouseLiq.sol (L322-338)
```text
        // settle all positive pnl
        for (uint32 i = 0; i < v.perpIds.length; ++i) {
            uint32 perpId = v.perpIds[i];
            IPerpEngine.Balance memory balance = perpEngine.getBalance(
                perpId,
                txn.liquidatee
            );
            if (balance.vQuoteBalance > 0) {
                _settlePnlAgainstLiquidator(
                    txn,
                    perpId,
                    balance.vQuoteBalance,
                    spotEngine,
                    perpEngine
                );
            }
        }
```

**File:** core/contracts/PerpEngineState.sol (L23-52)
```text
    function _updateBalance(
        State memory state,
        Balance memory balance,
        int128 balanceDelta,
        int128 vQuoteDelta
    ) internal pure {
        // pre update
        state.openInterest -= balance.amount.abs();
        int128 cumulativeFundingAmountX18 = (balance.amount > 0)
            ? state.cumulativeFundingLongX18
            : state.cumulativeFundingShortX18;
        int128 diffX18 = cumulativeFundingAmountX18 -
            balance.lastCumulativeFundingX18;
        int128 deltaQuote = vQuoteDelta - diffX18.mul(balance.amount);

        // apply delta
        balance.amount += balanceDelta;

        // apply vquote
        balance.vQuoteBalance += deltaQuote;

        // post update
        if (balance.amount > 0) {
            state.openInterest += balance.amount;
            balance.lastCumulativeFundingX18 = state.cumulativeFundingLongX18;
        } else {
            state.openInterest -= balance.amount;
            balance.lastCumulativeFundingX18 = state.cumulativeFundingShortX18;
        }
    }
```

**File:** core/contracts/PerpEngine.sol (L88-100)
```text
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
```

**File:** core/contracts/PerpEngine.sol (L125-139)
```text
    function getSettlementState(uint32 productId, bytes32 subaccount)
        public
        returns (
            int128 availableSettle,
            State memory state,
            Balance memory balance
        )
    {
        (state, balance) = getStateAndBalance(productId, subaccount);

        availableSettle = MathHelper.min(
            calculatePositionPnl(balance, productId),
            state.availableSettle
        );
    }
```
