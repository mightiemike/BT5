### Title
Missing Parent Subaccount Health Check After Margin Transfer in `createIsolatedSubaccount` — (`core/contracts/OffchainExchange.sol`)

---

### Summary

`createIsolatedSubaccount` deducts margin from the parent subaccount's quote balance and credits it to the new isolated subaccount, but performs **no health check on the parent** after the deduction. Every other collateral-moving operation in the protocol enforces a post-transfer initial health check on the source. This omission allows a parent subaccount to become undercollateralized immediately after isolated subaccount creation.

---

### Finding Description

In `createIsolatedSubaccount`, after the margin transfer:

```solidity
spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.order.sender, -margin);
spotEngine.updateBalance(QUOTE_PRODUCT_ID, newIsolatedSubaccount, margin);
```

there is no subsequent call to `getHealth(txn.order.sender, INITIAL)` or `_isAboveInitial(txn.order.sender)`. [1](#0-0) 

Compare this to every analogous collateral-moving path in the protocol:

- `transferQuote` → `require(_isAboveInitial(txn.sender), ERR_SUBACCT_HEALTH)` after deduction [2](#0-1) 

- `withdrawCollateral` → `require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH)` after deduction [3](#0-2) 

- `mintNlp` → `require(getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0, ERR_SUBACCT_HEALTH)` after deduction [4](#0-3) 

The `isHealthy` virtual function used in `matchOrders` always returns `true` in the base contract and is not called in `createIsolatedSubaccount` at all. [5](#0-4) [6](#0-5) 

---

### Impact Explanation

A parent subaccount holding open perp positions (or any position requiring margin) can have its entire quote collateral drained into an isolated subaccount. The parent's health immediately drops below zero. The protocol's solvency invariant — that no collateral transfer may leave the source subaccount undercollateralized — is broken. The parent becomes eligible for liquidation at a loss to the insurance fund, and the isolated subaccount retains the transferred margin, creating or destroying value incorrectly.

---

### Likelihood Explanation

The path is fully externally reachable: a user submits a signed `CreateIsolatedSubaccount` transaction through the standard endpoint flow (`EndpointTx.sol` → `OffchainExchange.createIsolatedSubaccount`). No admin privileges, sequencer compromise, or special configuration is required. The attacker only needs to hold a parent subaccount with open positions and craft an order with `margin` equal to the parent's available quote balance. [7](#0-6) 

---

### Recommendation

Add an initial health check on the parent subaccount immediately after the margin transfer, consistent with all other collateral-moving operations:

```solidity
if (margin > 0) {
    digestToMargin[digest] = margin;
    spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.order.sender, -margin);
    spotEngine.updateBalance(QUOTE_PRODUCT_ID, newIsolatedSubaccount, margin);
    // ADD: enforce parent remains solvent
    require(
        IClearinghouse(clearinghouse).getHealth(
            txn.order.sender,
            IProductEngine.HealthType.INITIAL
        ) >= 0,
        ERR_SUBACCT_HEALTH
    );
}
```

---

### Proof of Concept

1. Deploy the protocol on a local Hardhat fork.
2. Create a parent subaccount with open perp positions such that its initial health is exactly `H ≥ 0` with quote balance `Q` (i.e., removing `Q` quote would make health negative).
3. Craft a `CreateIsolatedSubaccount` order with `_isolatedMargin(appendix) == Q` (set the top 64 bits of `appendix` to `Q / 1e12`).
4. Submit the transaction through the endpoint.
5. Call `clearinghouse.getHealth(parentSubaccount, INITIAL)`.
6. Assert the returned value is `< 0`.

The transaction succeeds (no revert), the parent's quote balance is now `0`, and its health is negative — the parent is undercollateralized with no check having prevented it. [8](#0-7)

### Citations

**File:** core/contracts/OffchainExchange.sol (L625-629)
```text
    function isHealthy(
        bytes32 /* subaccount */
    ) internal view virtual returns (bool) {
        return true;
    }
```

**File:** core/contracts/OffchainExchange.sol (L826-827)
```text
        require(isHealthy(taker.order.sender), ERR_INVALID_TAKER);
        require(isHealthy(maker.order.sender), ERR_INVALID_MAKER);
```

**File:** core/contracts/OffchainExchange.sol (L999-1007)
```text
    function createIsolatedSubaccount(
        IEndpoint.CreateIsolatedSubaccount memory txn,
        address linkedSigner
    ) external onlyEndpoint returns (bytes32) {
        require(
            !RiskHelper.isIsolatedSubaccount(txn.order.sender),
            ERR_UNAUTHORIZED
        );
        require(_isIsolated(txn.order.appendix), ERR_UNAUTHORIZED);
```

**File:** core/contracts/OffchainExchange.sol (L1074-1089)
```text
        int128 margin = int128(_isolatedMargin(txn.order.appendix));
        if (margin > 0) {
            digestToMargin[digest] = margin;
            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                txn.order.sender,
                -margin
            );
            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                newIsolatedSubaccount,
                margin
            );
        }

        return newIsolatedSubaccount;
```

**File:** core/contracts/Clearinghouse.sol (L247-249)
```text
        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -toTransfer);
        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.recipient, toTransfer);
        require(_isAboveInitial(txn.sender), ERR_SUBACCT_HEALTH);
```

**File:** core/contracts/Clearinghouse.sol (L415-419)
```text
        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
```

**File:** core/contracts/Clearinghouse.sol (L479-482)
```text
        require(
            getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0,
            ERR_SUBACCT_HEALTH
        );
```
