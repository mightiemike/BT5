### Title
Token Address Replacement in `addOrUpdateProduct` Desynchronizes Decimal Scaling from Existing Normalized Balances — (File: `core/contracts/SpotEngine.sol`)

---

### Summary

`SpotEngine.addOrUpdateProduct` unconditionally overwrites `configs[productId]` — including the `token` address — for both new and existing products. Because `Clearinghouse._decimals()` reads decimals live from the current token at deposit/withdrawal time, replacing the token with one of different decimals permanently desynchronizes the decimal multiplier from the one used when existing balances were recorded. All subsequent deposits and withdrawals use the wrong scaling factor relative to pre-existing normalized balances, enabling accounting corruption and potential fund loss.

---

### Finding Description

`SpotEngine.addOrUpdateProduct` is callable by the owner for both new and existing products. For existing products, `_addOrUpdateProduct` returns `isNewProduct = false` and skips re-initialization of the `State`, but the outer function unconditionally writes the entire new `Config` struct — including `token` — to storage: [1](#0-0) 

The existing normalized balances in `states[productId]` (`totalDepositsNormalized`, `cumulativeDepositsMultiplierX18`, etc.) and `balances[productId][subaccount]` (`amountNormalized`) are **not adjusted**.

At deposit time, `Clearinghouse.depositCollateral` reads the decimal count live from the current token: [2](#0-1) 

At withdrawal time, the same live read occurs: [3](#0-2) 

Both paths call `_decimals`, which resolves the token address from the current `configs[productId].token`: [4](#0-3) 

If the token is replaced with one of different decimals, the multiplier `10^(MAX_DECIMALS - decimals)` changes, but all previously recorded `amountNormalized` values were computed with the old multiplier. The two sets of balances are now on incompatible scales.

---

### Impact Explanation

**Scenario — token replaced from 6-decimal to 18-decimal:**

1. Product 1 is live with USDC (6 decimals). Alice deposits 1,000 USDC → `amountRealized = 1000 × 10^12` internal units stored in `balances[1][alice]`.
2. Owner calls `addOrUpdateProduct(1, ..., Config({token: newToken18Dec, ...}), ...)`. `configs[1].token` is now the 18-decimal token; Alice's `amountNormalized` is unchanged at `1000 × 10^12`.
3. Alice submits a withdrawal of `amount = 1000 × 10^12` raw units of the new token. `_decimals(1)` returns 18, so `multiplier = 1` and `amountRealized = -(1000 × 10^12)`. Alice's internal balance is zeroed out and she receives `1000 × 10^12` units of the new token — `10^12` times more value than she deposited.

**Scenario — token replaced from 18-decimal to 6-decimal:**

Existing users' internal balances (scaled by `10^0 = 1`) are now interpreted with a multiplier of `10^12`. Any withdrawal attempt deducts `amount × 10^12` from the internal balance, which immediately exceeds the user's actual balance, causing the health check to fail. Users' funds are effectively locked.

In both cases the broken invariant is: **the total internal balance of all users must equal the actual token holdings of the Clearinghouse, scaled by the current multiplier**. After a token swap this invariant is violated for all pre-existing balances.

---

### Likelihood Explanation

The owner must call `addOrUpdateProduct` on an existing product with a different `token` address. This can occur:

- Inadvertently, when updating interest-rate parameters (`interestFloorX18`, `interestSmallCapX18`, etc.) while accidentally supplying a different token address in the `Config` struct.
- Intentionally, as part of a token migration, without realizing that existing normalized balances are not re-scaled.

The function provides no guard preventing a token address change on a live product with non-zero deposits. The external report's analog (`setReserveDecimals` in Aave) is structurally identical: an admin function that changes a decimal-related parameter without propagating it to dependent state.

---

### Recommendation

In `SpotEngine.addOrUpdateProduct`, when updating an existing product, enforce that the token address cannot change:

```solidity
if (!isNewProduct) {
    require(
        config.token == configs[productId].token,
        "ERR_TOKEN_IMMUTABLE"
    );
}
```

Alternatively, only permit a token address change when `states[productId].totalDepositsNormalized == 0` and `states[productId].totalBorrowsNormalized == 0`, ensuring no existing balances are affected.

---

### Proof of Concept

1. Product 1 is initialized with `tokenA` (6 decimals, e.g. USDC). `MAX_DECIMALS = 18`, so `multiplier = 10^12`.
2. Alice deposits 1,000 USDC via `Endpoint → Clearinghouse.depositCollateral`. `amountRealized = 1000 × 10^12`. `balances[1][alice].amountNormalized` is set accordingly.
3. Owner calls `SpotEngine.addOrUpdateProduct(1, quoteId, sizeIncrement, minSize, Config({token: tokenB, ...}), riskStore)` where `tokenB` has 18 decimals. `configs[1].token = tokenB`. Alice's `amountNormalized` is unchanged.
4. Owner (or protocol) transfers sufficient `tokenB` to the Clearinghouse (e.g. as part of a migration).
5. Alice submits `WithdrawCollateral` with `amount = 1000 × 10^12`. `_decimals(1)` returns 18, `multiplier = 1`, `amountRealized = -(1000 × 10^12)`. Alice's balance is zeroed. She receives `1000 × 10^12` units of `tokenB` — `10^12×` her deposited value — draining the contract. [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** core/contracts/SpotEngine.sol (L68-97)
```text
    function addOrUpdateProduct(
        uint32 productId,
        uint32 quoteId,
        int128 sizeIncrement,
        int128 minSize,
        Config calldata config,
        RiskHelper.RiskStore calldata riskStore
    ) public onlyOwner {
        bool isNewProduct = _addOrUpdateProduct(
            productId,
            quoteId,
            sizeIncrement,
            minSize,
            riskStore
        );
        configs[productId] = config;

        if (isNewProduct) {
            require(productId != QUOTE_PRODUCT_ID);
            _setState(
                productId,
                State({
                    cumulativeDepositsMultiplierX18: ONE,
                    cumulativeBorrowsMultiplierX18: ONE,
                    totalDepositsNormalized: 0,
                    totalBorrowsNormalized: 0
                })
            );
        }
    }
```

**File:** core/contracts/Clearinghouse.sol (L183-208)
```text
    function _tokenAddress(uint32 productId) internal view returns (address) {
        return _spotEngine().getConfig(productId).token;
    }

    function _decimals(uint32 productId) internal virtual returns (uint8) {
        IERC20Base token = IERC20Base(_tokenAddress(productId));
        require(address(token) != address(0), ERR_INVALID_PRODUCT);
        return token.decimals();
    }

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
```

**File:** core/contracts/Clearinghouse.sol (L391-421)
```text
    function withdrawCollateral(
        bytes32 sender,
        uint32 productId,
        uint128 amount,
        address sendTo,
        uint64 idx
    ) public virtual onlyEndpoint {
        require(!RiskHelper.isIsolatedSubaccount(sender), ERR_UNAUTHORIZED);
        require(amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        ISpotEngine spotEngine = _spotEngine();
        IERC20Base token = IERC20Base(spotEngine.getConfig(productId).token);
        require(address(token) != address(0));

        if (sendTo == address(0)) {
            sendTo = address(uint160(bytes20(sender)));
        }

        handleWithdrawTransfer(token, sendTo, amount, idx);

        int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
        int128 amountRealized = -int128(amount) * int128(multiplier);
        spotEngine.updateBalance(productId, sender, amountRealized);
        spotEngine.assertUtilization(productId);

        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
        emit ModifyCollateral(amountRealized, sender, productId);
    }
```
