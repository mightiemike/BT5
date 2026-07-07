### Title
Hardcoded Quote Token Price at $1.00 Enables Over-Borrowing During Stablecoin Depeg — (`File: core/contracts/SpotEngine.sol`, `core/contracts/Endpoint.sol`, `core/contracts/Clearinghouse.sol`)

---

### Summary

The Nado protocol hardcodes the price of the quote token (`QUOTE_PRODUCT_ID = 0`, the protocol's USDC-equivalent settlement asset) to exactly `ONE` (1e18 = $1.00) in three independent locations. This price is never updated by any oracle or sequencer feed. If the quote stablecoin de-pegs, the protocol continues to value it at $1.00, allowing users to borrow against over-valued collateral and leave the protocol insolvent.

---

### Finding Description

**Location 1 — `Endpoint.initialize()`:**

`priceX18[QUOTE_PRODUCT_ID]` is set to `ONE` at initialization and is never written again anywhere in the codebase. The `EndpointTx` only updates `priceX18[NLP_PRODUCT_ID]` during mint/burn operations; no code path updates `priceX18[QUOTE_PRODUCT_ID]`. [1](#0-0) 

**Location 2 — `SpotEngine.initialize()`:**

The engine's `RiskStore` for `QUOTE_PRODUCT_ID` is initialized with `priceX18: ONE`. This is the value used in all health contribution calculations for the quote token balance. [2](#0-1) 

**Location 3 — `Clearinghouse.checkMinDeposit()`:**

The minimum deposit check explicitly hardcodes `priceX18 = ONE` for the quote product, bypassing any oracle lookup entirely:

```solidity
int128 priceX18 = ONE;
if (productId != QUOTE_PRODUCT_ID) {
    priceX18 = _getPriceX18(productId);
}
``` [3](#0-2) 

The `updatePrice` transaction path in `Clearinghouse` routes through `productToEngine[txn.productId]` and calls `engine.updatePrice()`. Even if this could update the engine's risk store for the quote product, `Endpoint.priceX18[QUOTE_PRODUCT_ID]` remains permanently `ONE` and `checkMinDeposit` ignores any engine price for the quote product regardless. [4](#0-3) 

---

### Impact Explanation

If the quote stablecoin (USDC) de-pegs to, e.g., $0.95:

1. A user deposits 10,000 USDC (real value: $9,500).
2. The protocol values the deposit at $10,000 (hardcoded `ONE`).
3. The user borrows other spot/perp collateral up to the health limit derived from the $10,000 valuation.
4. The user defaults or withdraws, leaving the protocol holding 10,000 USDC worth only $9,500.
5. The protocol is insolvent by $500 per 10,000 USDC deposited.

Because the quote token is the universal unit of account for all health calculations, all subaccounts simultaneously become over-leveraged during a depeg event, and the protocol cannot liquidate them correctly since liquidation payments are also denominated in the same mispriced quote token. [5](#0-4) 

---

### Likelihood Explanation

- USDC has de-pegged before (March 2023 SVB event: briefly to $0.87).
- The protocol has no circuit breaker, no oracle integration for the quote token, and no admin function to update `priceX18[QUOTE_PRODUCT_ID]` in `Endpoint`.
- Any user can exploit this permissionlessly via the standard `depositCollateral` → borrow flow the moment a depeg occurs.
- Likelihood: **Medium** (depeg events are rare but historically documented; the exploit window is the duration of the depeg).

---

### Recommendation

1. Integrate a Chainlink or equivalent oracle for the quote token price and feed it into both `Endpoint.priceX18[QUOTE_PRODUCT_ID]` and the `SpotEngine` risk store via the existing `updatePrice` sequencer transaction.
2. Remove the hardcoded `priceX18 = ONE` branch in `Clearinghouse.checkMinDeposit()` and route it through `_getPriceX18(productId)` uniformly.
3. Add a circuit breaker that halts deposits/borrows if the quote token price deviates beyond a configurable threshold (e.g., ±2%).

---

### Proof of Concept

```
1. USDC de-pegs to $0.90 (as occurred during the March 2023 SVB event).

2. Attacker calls depositCollateral(productId=0, amount=100_000e6) 
   depositing 100,000 USDC (real value: $90,000).

3. Protocol records balance at 100,000 * ONE = $100,000 (hardcoded).

4. Attacker calls depositCollateral + borrowing flow for ETH spot product.
   Health check: quote balance = +$100,000 (hardcoded ONE price).
   Attacker borrows $80,000 worth of ETH (within initial health margin).

5. Attacker withdraws ETH. Protocol holds 100,000 USDC worth $90,000.
   Net loss to protocol: $10,000 on this single position.

6. At scale across all depositors during a depeg, the protocol becomes
   systemically insolvent as all quote-denominated health values are inflated.
``` [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** core/contracts/Endpoint.sol (L54-54)
```text
        priceX18[QUOTE_PRODUCT_ID] = ONE;
```

**File:** core/contracts/SpotEngine.sol (L32-38)
```text
        _risk().value[QUOTE_PRODUCT_ID] = RiskHelper.RiskStore({
            longWeightInitial: 1e9,
            shortWeightInitial: 1e9,
            longWeightMaintenance: 1e9,
            shortWeightMaintenance: 1e9,
            priceX18: ONE
        });
```

**File:** core/contracts/Clearinghouse.sol (L358-375)
```text
    function updatePrice(bytes calldata transaction)
        external
        onlyEndpoint
        returns (uint32, int128)
    {
        IEndpoint.UpdatePrice memory txn = abi.decode(
            transaction[1:],
            (IEndpoint.UpdatePrice)
        );
        require(txn.priceX18 > 0, ERR_INVALID_PRICE);
        IProductEngine engine = productToEngine[txn.productId];
        if (address(engine) != address(0)) {
            engine.updatePrice(txn.productId, txn.priceX18);
            return (txn.productId, txn.priceX18);
        } else {
            return (0, 0);
        }
    }
```

**File:** core/contracts/Clearinghouse.sol (L709-714)
```text
        int128 priceX18 = ONE;
        if (productId != QUOTE_PRODUCT_ID) {
            priceX18 = _getPriceX18(productId);
        }

        return priceX18.mul(amountRealized) >= minDepositAmount;
```

**File:** core/contracts/ClearinghouseLiq.sol (L511-536)
```text
            );

            v.liquidationPayment = v.liquidationPriceX18.mul(txn.amount);
            v.liquidationFees = (v.oraclePriceX18 - v.liquidationPriceX18)
                .mul(LIQUIDATION_FEE_FRACTION)
                .mul(txn.amount);

            spotEngine.updateBalance(
                txn.productId,
                txn.liquidatee,
                -txn.amount
            );

            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                txn.liquidatee,
                v.liquidationPayment
            );

            spotEngine.updateBalance(txn.productId, txn.sender, txn.amount);

            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                txn.sender,
                -v.liquidationPayment - v.liquidationFees
            );
```
