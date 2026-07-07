### Title
Non-Finalize Liquidation of Isolated Subaccount Bypasses `tryCloseIsolatedSubaccount`, Permanently Stranding Funds in Isolated Subaccount — (`core/contracts/ClearinghouseLiq.sol`)

---

### Summary

`liquidateSubaccountImpl` guards against isolated subaccounts acting as the *sender/liquidator* but places **no guard** against an isolated subaccount being the *liquidatee* in a non-finalize liquidation (`productId != type(uint32).max`). The `tryCloseIsolatedSubaccount` call — which returns the isolated subaccount's `vQuoteBalance` and spot USDC to the parent — is gated entirely behind `_finalizeSubaccount` returning `true`. A liquidator can fully liquidate an isolated subaccount's perp position via the non-finalize path, leaving the subaccount with zero position but non-zero balances that are permanently stranded.

---

### Finding Description

**Entry point:** `ClearinghouseLiq.liquidateSubaccountImpl` — callable by any sequencer-submitted `LiquidateSubaccount` transaction.

**Guard analysis in `liquidateSubaccountImpl` (lines 598–647):**

- Line 601 blocks isolated subaccounts as *senders*:
  ```solidity
  require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);
  ``` [1](#0-0) 

- Lines 604–607 only block `X_ACCOUNT` and `N_ACCOUNT` as liquidatees — **no check blocks an isolated subaccount as liquidatee**: [2](#0-1) 

- Lines 620–627: `tryCloseIsolatedSubaccount` is called **only** when `_finalizeSubaccount` returns `true`:
  ```solidity
  if (_finalizeSubaccount(txn, spotEngine, perpEngine)) {
      if (RiskHelper.isIsolatedSubaccount(txn.liquidatee)) {
          IOffchainExchange(...).tryCloseIsolatedSubaccount(txn.liquidatee);
      }
      return;
  }
  ``` [3](#0-2) 

- `_finalizeSubaccount` returns `false` immediately when `productId != type(uint32).max`:
  ```solidity
  if (txn.productId != type(uint32).max) {
      return false;
  }
  ``` [4](#0-3) 

So for any non-finalize liquidation, execution falls through to `_handleLiquidationPayment` with no isolated-subaccount awareness.

**What `_tryCloseIsolatedSubaccount` does (and why it matters):**

When `balance.amount == 0`, it transfers `vQuoteBalance` and spot USDC from the isolated subaccount back to the parent, then clears the slot:

```solidity
if (balance.amount == 0) {
    // transfer vQuoteBalance to parent
    // transfer spot USDC to parent
    isolatedSubaccountsMask[addr] &= ~uint256(0) ^ (1 << id);
    isolatedSubaccounts[parent][id] = bytes32(0);
    parentSubaccounts[subaccount] = bytes32(0);
}
``` [5](#0-4) 

This is the **only** mechanism to return funds from an isolated subaccount to its parent. It is never triggered in the non-finalize liquidation path.

---

### Impact Explanation

**Stranded funds (direct asset loss):**

After a non-finalize liquidation fully reduces the isolated subaccount's perp position to zero:
- `vQuoteBalance` (accumulated funding / unrealized PnL settled during liquidation) remains in the isolated subaccount
- Spot USDC balance remains in the isolated subaccount
- Neither is ever returned to the parent

If the resulting health (`spot USDC + vQuoteBalance`) is positive, the isolated subaccount is now healthy. No finalize liquidation (`productId = type(uint32).max`) can be submitted because `isUnderMaintenance` will return `false`. The funds are permanently unrecoverable by the user, since `tryCloseIsolatedSubaccount` requires `msg.sender == endpoint || msg.sender == clearinghouse` and cannot be called directly by the user. [6](#0-5) 

**Slot exhaustion (secondary):**

The `isolatedSubaccountsMask` bit and `isolatedSubaccounts` mapping entry are never cleared, permanently occupying one of the user's isolated subaccount slots. [7](#0-6) 

---

### Likelihood Explanation

The scenario is realistic in volatile markets:

1. An isolated subaccount opens a long perp position. Funding payments accumulate a positive `vQuoteBalance`.
2. The oracle price drops; the position's mark-to-market loss drives total health negative → subaccount is under maintenance.
3. A liquidator submits `liquidateSubaccountImpl` with `productId = <perp_id>` (not `type(uint32).max`) and `amount = full_position`.
4. `_handleLiquidationPayment` closes the position; the liquidatee receives `liquidationPayment` into `vQuoteBalance`.
5. Post-liquidation: `perp.amount = 0`, `vQuoteBalance = prior_vQuoteBalance + liquidationPayment > 0`, spot USDC unchanged.
6. If `spot USDC + vQuoteBalance > 0`, the subaccount is healthy. `tryCloseIsolatedSubaccount` is never called. Funds are stranded.

The liquidator has no incentive to use the finalize path — the non-finalize path is cheaper and still earns the liquidation discount.

---

### Recommendation

Add a guard in `liquidateSubaccountImpl` that prevents non-finalize liquidations against isolated subaccounts, forcing liquidators to use the finalize path (`productId == type(uint32).max`) which correctly calls `tryCloseIsolatedSubaccount`:

```solidity
// In liquidateSubaccountImpl, after the existing sender check:
require(
    !RiskHelper.isIsolatedSubaccount(txn.liquidatee) ||
    txn.productId == type(uint32).max,
    ERR_UNAUTHORIZED
);
```

Alternatively, call `_tryCloseIsolatedSubaccount` (or `tryCloseIsolatedSubaccount`) unconditionally after `_handleLiquidationPayment` whenever the liquidatee is an isolated subaccount and the resulting perp position is zero.

---

### Proof of Concept

```
1. Deploy protocol with an isolated subaccount `iso` (parent `par`) holding:
   - perpEngine.balance[productId][iso].amount = 100e18
   - perpEngine.balance[productId][iso].vQuoteBalance = 20e18  (positive funding)
   - spotEngine.balance[QUOTE][iso].amount = -5e18             (negative USDC)
   → health = -5 + 20 + 100 * weight - 100 * oracle < 0  (under maintenance)

2. Liquidator calls liquidateSubaccountImpl({
       sender: liquidator,
       liquidatee: iso,
       productId: <perp_product_id>,   // NOT type(uint32).max
       amount: 100e18,
       ...
   })

3. _finalizeSubaccount returns false (productId != type(uint32).max).
   _handleLiquidationPayment executes:
   - iso.perp.amount → 0
   - iso.perp.vQuoteBalance → 20e18 + liquidationPayment (e.g., 90e18) = 110e18
   - iso.spot.USDC unchanged = -5e18

4. Post-liquidation health of iso = -5 + 110 = 105 > 0 → healthy.

5. Assert: tryCloseIsolatedSubaccount was never called.
   Assert: parentSubaccounts[iso] != bytes32(0)  (slot not cleared)
   Assert: spotEngine.balance[QUOTE][iso].amount = -5e18  (USDC stranded)
   Assert: perpEngine.balance[productId][iso].vQuoteBalance = 110e18  (vQuote stranded)
   Assert: spotEngine.balance[QUOTE][par].amount unchanged  (parent never received funds)

6. Attempt finalize liquidation (productId = type(uint32).max) → reverts with
   ERR_NOT_LIQUIDATABLE because iso is now healthy.

→ 105e18 USDC-equivalent permanently stranded in isolated subaccount.
```

### Citations

**File:** core/contracts/ClearinghouseLiq.sol (L284-286)
```text
        if (txn.productId != type(uint32).max) {
            return false;
        }
```

**File:** core/contracts/ClearinghouseLiq.sol (L601-601)
```text
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);
```

**File:** core/contracts/ClearinghouseLiq.sol (L604-607)
```text
        require(
            txn.liquidatee != X_ACCOUNT && txn.liquidatee != N_ACCOUNT,
            ERR_NOT_LIQUIDATABLE
        );
```

**File:** core/contracts/ClearinghouseLiq.sol (L620-627)
```text
        if (_finalizeSubaccount(txn, spotEngine, perpEngine)) {
            if (RiskHelper.isIsolatedSubaccount(txn.liquidatee)) {
                IOffchainExchange(
                    IEndpoint(getEndpoint()).getOffchainExchange()
                ).tryCloseIsolatedSubaccount(txn.liquidatee);
            }
            return;
        }
```

**File:** core/contracts/OffchainExchange.sol (L152-158)
```text
    function tryCloseIsolatedSubaccount(bytes32 subaccount) external virtual {
        require(
            msg.sender == getEndpoint() || msg.sender == address(clearinghouse),
            ERR_UNAUTHORIZED
        );
        _tryCloseIsolatedSubaccount(subaccount);
    }
```

**File:** core/contracts/OffchainExchange.sol (L169-207)
```text
        if (balance.amount == 0) {
            uint8 id = RiskHelper.getIsolatedId(subaccount);
            address addr = address(uint160(bytes20(subaccount)));
            bytes32 parent = parentSubaccounts[subaccount];
            if (balance.vQuoteBalance != 0) {
                perpEngine.updateBalance(
                    productId,
                    subaccount,
                    0,
                    -balance.vQuoteBalance
                );
                perpEngine.updateBalance(
                    productId,
                    parent,
                    0,
                    balance.vQuoteBalance
                );
            }
            int128 quoteBalance = spotEngine
                .getBalance(QUOTE_PRODUCT_ID, subaccount)
                .amount;
            if (quoteBalance != 0) {
                spotEngine.updateBalance(
                    QUOTE_PRODUCT_ID,
                    subaccount,
                    -quoteBalance
                );
                spotEngine.updateBalance(
                    QUOTE_PRODUCT_ID,
                    parent,
                    quoteBalance
                );
            }
            isolatedSubaccountsMask[addr] &= ~uint256(0) ^ (1 << id);
            isolatedSubaccounts[parent][id] = bytes32(0);
            parentSubaccounts[subaccount] = bytes32(0);

            emit CloseIsolatedSubaccount(subaccount, parent);
        }
```
