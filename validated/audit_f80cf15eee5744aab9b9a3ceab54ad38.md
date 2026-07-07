### Title
`DirectDepositV1.creditDeposit()` Uses `balanceOf(address(this))` as Per-Product Deposit Amount, Causing Full Deposit Misattribution When Multiple Spot Products Share the Same Token — (File: `core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` iterates over all registered spot product IDs and uses `token.balanceOf(address(this))` to determine how much to deposit for each product. If two spot products are configured with the same underlying token address, the first product in the iteration order consumes the entire token balance. The second product then observes a zero balance and receives no deposit. This is a structural analog of the XVS vault bug: a shared on-chain balance is used as a per-slot accounting figure, causing the second slot to be permanently starved.

---

### Finding Description

In `DirectDepositV1.creditDeposit()`, the function fetches all product IDs from the spot engine and loops over them:

```solidity
uint32[] memory productIds = spotEngine.getProductIds();
for (uint256 i = 0; i < productIds.length; i++) {
    uint32 productId = productIds[i];
    address tokenAddr = spotEngine.getToken(productId);
    ...
    IIERC20Base token = IIERC20Base(tokenAddr);
    uint256 balance = token.balanceOf(address(this));   // <-- shared balance
    if (balance != 0) {
        token.approve(address(endpoint), balance);
        endpoint.depositCollateralWithReferral(
            subaccount, productId, uint128(balance), "-1"
        );
    }
}
``` [1](#0-0) 

The `depositCollateralWithReferral` call causes the endpoint to pull `balance` tokens from the DDA via `transferFrom` (evidenced by the preceding `token.approve(address(endpoint), balance)` call). After the first iteration for a given token address, the DDA's balance of that token is zero. Any subsequent product that maps to the same token address reads `balance = 0` and is silently skipped.

`SpotEngine.addOrUpdateProduct()` stores the token address in `configs[productId]` with no uniqueness check on the token field:

```solidity
configs[productId] = config;
``` [2](#0-1) 

`ContractOwner.submitSpotAddOrUpdateProductCall()` only guards against duplicate `productId` values, not duplicate token addresses:

```solidity
for (uint256 i = 0; i < pendingIds.length; i++) {
    require(productId != pendingIds[i], "dup spot");
}
``` [3](#0-2) 

There is therefore no on-chain invariant preventing two spot products from sharing the same token.

---

### Impact Explanation

When two spot products share the same token:

- **Product A** (first in `productIds` array): receives 100% of the DDA's token balance.
- **Product B** (second in `productIds` array): receives 0, regardless of how many tokens were sent to the DDA.

A user who sends tokens to the DDA intending them to be credited to Product B will have their entire deposit silently attributed to Product A. Because `SpotEngine` tracks balances per `(productId, subaccount)` pair, the user's balance in Product B remains zero. If the user holds a borrow position in Product B, the intended repayment never arrives, the borrow persists, and the user's health can deteriorate to the point of liquidation — a concrete asset loss. Additionally, `assertUtilization` for Product B will continue to see zero deposits, blocking any future borrow activity on that product entirely. [4](#0-3) 

---

### Likelihood Explanation

**Medium-low.** The precondition — two spot products sharing the same token — is not a standard deployment pattern, but there is no on-chain guard preventing it. The `addOrUpdateProduct` path is owner-gated, so the precondition requires an admin configuration choice (intentional or mistaken). Once that configuration exists, the misattribution is triggered by any unprivileged caller invoking `creditDeposit()`, which is a public `external` function with no access control. [5](#0-4) 

---

### Recommendation

Replace the `balanceOf`-based deposit amount with a per-product tracked balance. One approach: before the loop, snapshot the balance of each distinct token address once, then subtract amounts already deposited in earlier iterations. Alternatively, enforce a uniqueness constraint on `config.token` inside `SpotEngine.addOrUpdateProduct()` so that no two products can ever share the same token address, eliminating the root condition entirely.

---

### Proof of Concept

1. Admin registers **Product A** (`productId = 1`, token = `TOKEN_X`).
2. Admin registers **Product B** (`productId = 2`, token = `TOKEN_X` — same address).
3. `spotEngine.getProductIds()` returns `[0 (quote), 1, 2]`.
4. User sends 100 `TOKEN_X` to the DDA and calls `creditDeposit()`.
5. **Iteration i=1 (Product A):** `TOKEN_X.balanceOf(dda) = 100`. DDA approves endpoint for 100, endpoint pulls 100 via `transferFrom`. DDA balance → 0. `spotEngine` credits 100 to `(productId=1, subaccount)`.
6. **Iteration i=2 (Product B):** `TOKEN_X.balanceOf(dda) = 0`. Condition `balance != 0` is false; loop body skipped. `spotEngine` balance for `(productId=2, subaccount)` remains 0.
7. If the user held a borrow in Product B, the intended repayment never occurs. Health degrades; liquidation becomes possible. [1](#0-0) [6](#0-5)

### Citations

**File:** core/contracts/DirectDepositV1.sol (L83-100)
```text
    function creditDeposit() external {
        uint32[] memory productIds = spotEngine.getProductIds();
        for (uint256 i = 0; i < productIds.length; i++) {
            uint32 productId = productIds[i];
            address tokenAddr = spotEngine.getToken(productId);
            require(tokenAddr != address(0), "Invalid productId.");
            IIERC20Base token = IIERC20Base(tokenAddr);
            uint256 balance = token.balanceOf(address(this));
            if (balance != 0) {
                token.approve(address(endpoint), balance);
                endpoint.depositCollateralWithReferral(
                    subaccount,
                    productId,
                    uint128(balance),
                    "-1"
                );
            }
        }
```

**File:** core/contracts/SpotEngine.sol (L83-83)
```text
        configs[productId] = config;
```

**File:** core/contracts/SpotEngine.sol (L232-241)
```text
    function assertUtilization(uint32 productId) external view {
        (State memory _state, ) = getStateAndBalance(productId, X_ACCOUNT);
        int128 totalDeposits = _state.totalDepositsNormalized.mul(
            _state.cumulativeDepositsMultiplierX18
        );
        int128 totalBorrows = _state.totalBorrowsNormalized.mul(
            _state.cumulativeBorrowsMultiplierX18
        );
        require(totalDeposits >= totalBorrows, ERR_MAX_UTILIZATION);
    }
```

**File:** core/contracts/ContractOwner.sol (L99-102)
```text
        uint32[] memory pendingIds = pendingSpotAddOrUpdateProductIds();
        for (uint256 i = 0; i < pendingIds.length; i++) {
            require(productId != pendingIds[i], "dup spot");
        }
```

**File:** core/contracts/SpotEngineState.sol (L10-12)
```text
    mapping(uint32 => Config) internal configs;
    mapping(uint32 => State) internal states;
    mapping(uint32 => mapping(bytes32 => BalanceNormalized)) internal balances;
```
