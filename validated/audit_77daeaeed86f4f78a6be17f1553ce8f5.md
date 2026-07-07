### Title
`DirectDepositV1.creditDeposit()` Permanently Reverts Due to NLP Product Token Incompatibility — (`File: core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` iterates over every product ID returned by `spotEngine.getProductIds()`, which includes `NLP_PRODUCT_ID = 11`. For each product, it hard-requires the token address to be non-zero. Unlike `SpotEngineState.updateStates()`, which explicitly skips `NLP_PRODUCT_ID`, `creditDeposit()` has no such guard. If the NLP product is configured with `token == address(0)` — a natural configuration for a purely internal accounting product whose balance is managed exclusively through `mintNlp`/`burnNlp` — the `require` reverts on every call, permanently blocking all token deposits through the DDA.

---

### Finding Description

`DirectDepositV1.creditDeposit()` is the public function that credits any ERC20 tokens held by the DDA into the Nado protocol:

```solidity
function creditDeposit() external {
    uint32[] memory productIds = spotEngine.getProductIds();
    for (uint256 i = 0; i < productIds.length; i++) {
        uint32 productId = productIds[i];
        address tokenAddr = spotEngine.getToken(productId);
        require(tokenAddr != address(0), "Invalid productId.");   // ← hard revert
        ...
    }
}
``` [1](#0-0) 

`NLP_PRODUCT_ID = 11` is a constant defined in `Constants.sol` and is registered in the spot engine's `productIds` array via `addOrUpdateProduct()`. [2](#0-1) 

The NLP product is a special internal accounting product. Its balance is managed exclusively through `Clearinghouse.mintNlp()` and `burnNlp()`, which credit/debit the `NLP_PRODUCT_ID` balance in exchange for QUOTE tokens. There is no mechanism that requires the NLP product to have a real ERC20 token address — it is an internal share-accounting product. [3](#0-2) 

The rest of the codebase already acknowledges this special status: `SpotEngineState.updateStates()` explicitly skips `NLP_PRODUCT_ID` to avoid applying interest-rate logic to it:

```solidity
if (productId == NLP_PRODUCT_ID) {
    continue;
}
``` [4](#0-3) 

`creditDeposit()` has no equivalent guard. When the loop reaches `NLP_PRODUCT_ID` and `configs[NLP_PRODUCT_ID].token == address(0)`, `spotEngine.getToken(NLP_PRODUCT_ID)` returns `address(0)`, and the `require` reverts. Because the revert is inside the loop and not caught, the entire `creditDeposit()` call fails — blocking deposits for every other valid product as well. [5](#0-4) 

---

### Impact Explanation

Every token sent to a DDA instance — USDC, WETH, or any other supported collateral — can only be credited to the protocol via `creditDeposit()`. If this function always reverts, all tokens sitting in the DDA are permanently inaccessible to the protocol. Users who sent tokens to the DDA expecting them to be deposited will find their funds stuck. The only recovery path is the `withdraw()` function, which is `onlyOwner`, meaning ordinary users have no self-service remedy. [6](#0-5) 

---

### Likelihood Explanation

`NLP_PRODUCT_ID` is confirmed to be in `productIds` (evidenced by `updateStates()` needing to skip it). Whether `configs[NLP_PRODUCT_ID].token` is `address(0)` depends on how the owner calls `addOrUpdateProduct()` for the NLP product. Since NLP is an internal accounting product with no real ERC20 deposit/withdraw flow, configuring it with `token = address(0)` is a natural and expected deployment choice. This is therefore a realistic, non-exotic configuration that would trigger the bug.

---

### Recommendation

Add an explicit skip for `NLP_PRODUCT_ID` inside `creditDeposit()`, mirroring the pattern already used in `SpotEngineState.updateStates()`:

```solidity
function creditDeposit() external {
    uint32[] memory productIds = spotEngine.getProductIds();
    for (uint256 i = 0; i < productIds.length; i++) {
        uint32 productId = productIds[i];
        if (productId == NLP_PRODUCT_ID) continue;   // add this guard
        address tokenAddr = spotEngine.getToken(productId);
        require(tokenAddr != address(0), "Invalid productId.");
        ...
    }
}
```

Alternatively, replace the hard `require` with a `continue` so that a zero-address product does not block deposits for all other products.

---

### Proof of Concept

1. Owner deploys the protocol and calls `SpotEngine.addOrUpdateProduct(NLP_PRODUCT_ID, ..., Config({ token: address(0), ... }), ...)`. This is a valid call — `NLP_PRODUCT_ID != QUOTE_PRODUCT_ID`, so the `require` in `addOrUpdateProduct` passes.
2. `NLP_PRODUCT_ID` is now in `spotEngine.getProductIds()` with `configs[11].token == address(0)`.
3. A user sends USDC to the DDA address.
4. Anyone calls `DirectDepositV1.creditDeposit()`.
5. The loop iterates over product IDs. When it reaches `productId = 11`, `spotEngine.getToken(11)` returns `address(0)`.
6. `require(tokenAddr != address(0), "Invalid productId.")` reverts.
7. The entire transaction reverts. The USDC remains stuck in the DDA.
8. Every subsequent call to `creditDeposit()` reverts identically. The DDA is permanently broken. [1](#0-0) [7](#0-6)

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

**File:** core/contracts/DirectDepositV1.sol (L103-106)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }
```

**File:** core/contracts/common/Constants.sol (L46-46)
```text
uint32 constant NLP_PRODUCT_ID = 11;
```

**File:** core/contracts/Clearinghouse.sol (L453-483)
```text
    function mintNlp(
        IEndpoint.MintNlp calldata txn,
        int128 oraclePriceX18,
        IEndpoint.NlpPool[] calldata nlpPools,
        int128[] calldata nlpPoolRebalanceX18
    ) external onlyEndpoint {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);

        ISpotEngine spotEngine = _spotEngine();
        spotEngine.updatePrice(NLP_PRODUCT_ID, oraclePriceX18);

        require(txn.quoteAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        int128 quoteAmount = int128(txn.quoteAmount);
        int128 nlpAmount = quoteAmount.div(oraclePriceX18);

        _validateNlpRebalance(nlpPools, nlpPoolRebalanceX18, quoteAmount);
        for (uint128 i = 0; i < nlpPoolRebalanceX18.length; i++) {
            require(nlpPoolRebalanceX18[i] >= 0, ERR_INVALID_NLP_REBALANCE);
        }

        spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, nlpAmount);
        spotEngine.updateBalance(NLP_PRODUCT_ID, N_ACCOUNT, -nlpAmount);

        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -quoteAmount);
        _applyNlpRebalance(spotEngine, nlpPools, nlpPoolRebalanceX18);

        require(
            getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0,
            ERR_SUBACCT_HEALTH
        );
    }
```

**File:** core/contracts/SpotEngineState.sol (L268-272)
```text
        for (uint32 i = 0; i < productIds.length; i++) {
            uint32 productId = productIds[i];
            if (productId == NLP_PRODUCT_ID) {
                continue;
            }
```

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

**File:** core/contracts/SpotEngine.sol (L290-292)
```text
    function getToken(uint32 productId) external view returns (address) {
        return address(configs[productId].token);
    }
```
