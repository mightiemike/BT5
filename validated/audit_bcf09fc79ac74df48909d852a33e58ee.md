### Title
Fee-on-Transfer Token Accounting Mismatch in Deposit Flow Inflates Subaccount Collateral Credit — (`File: core/contracts/EndpointStorage.sol`)

---

### Summary

`EndpointStorage.handleDepositTransfer` performs a two-hop token relay (user → Endpoint → Clearinghouse) using the same nominal `amount` for both legs. For fee-on-transfer tokens, the Clearinghouse receives fewer tokens than the amount encoded in the queued `SlowModeTx`. When that transaction is later executed, `Clearinghouse.depositCollateral` credits the full nominal `amount` to the subaccount, creating a permanent accounting surplus that exceeds the protocol's actual token holdings.

---

### Finding Description

The deposit entry path is:

1. `Endpoint.depositCollateral` / `depositCollateralWithReferral` calls `handleDepositTransfer(token, msg.sender, uint256(amount))`.
2. `handleDepositTransfer` executes two sequential transfers with the **same** `amount`:

```solidity
// EndpointStorage.sol lines 111-119
function handleDepositTransfer(IERC20Base token, address from, uint256 amount) internal {
    require(address(token) != address(0), ERR_INVALID_PRODUCT);
    safeTransferFrom(token, from, amount);          // hop 1: user → Endpoint
    safeTransferTo(token, address(clearinghouse), amount); // hop 2: Endpoint → Clearinghouse
}
```

3. A `SlowModeTx` is queued encoding the original `amount`:

```solidity
// Endpoint.sol lines 152-165
slowModeTxs[_slowModeConfig.txCount++] = SlowModeTx({
    ...
    tx: abi.encodePacked(
        uint8(TransactionType.DepositCollateral),
        abi.encode(DepositCollateral({ sender: subaccount, productId: productId, amount: amount }))
    )
});
```

4. When the sequencer executes the slow-mode transaction, `Clearinghouse.depositCollateral` credits the subaccount using the recorded `amount`:

```solidity
// Clearinghouse.sol lines 204-207
int128 amountRealized = int128(txn.amount) * int128(multiplier);
spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
```

**For a fee-on-transfer token**, hop 1 delivers `amount - fee` to the Endpoint. Hop 2 then attempts to forward the full `amount` to the Clearinghouse. This succeeds only when the Endpoint holds a pre-existing balance in that token (e.g., accumulated from `chargeSlowModeFee` calls, which transfer the quote token directly into the Endpoint). When it does succeed, the Clearinghouse receives `amount - fee` tokens but the subaccount is credited `amount * multiplier` — a permanent surplus in the internal ledger.

The `DirectDepositV1.creditDeposit()` path is also affected: it reads `balance = token.balanceOf(address(this))` and passes it verbatim as `amount`, so the same two-hop mismatch applies.

---

### Impact Explanation

**Impact: High**

Each successful deposit of a fee-on-transfer token inflates the credited collateral by `fee * multiplier` relative to the actual tokens held by the Clearinghouse. Over multiple deposits, the cumulative deficit grows. Because `withdrawCollateral` debits the subaccount balance and transfers the same nominal amount out of the Clearinghouse, the protocol becomes insolvent: the last withdrawers cannot be made whole. The Clearinghouse's real token balance is permanently lower than the sum of all credited subaccount balances for that product.

---

### Likelihood Explanation

**Likelihood: Low**

Two conditions must hold simultaneously:
1. A fee-on-transfer token must be listed as a supported collateral product.
2. The Endpoint must hold a pre-existing balance of that token (so hop 2 does not revert). This is plausible if the same token is used for slow-mode fees or if a prior failed/partial deposit left residual balance.

Neither condition is guaranteed by the current deployment, but neither is architecturally prevented. The protocol does not whitelist token types, and the Endpoint naturally accumulates token balances through `chargeSlowModeFee`.

---

### Recommendation

Replace the two-hop relay with a balance-delta check to credit only the tokens actually received:

```solidity
function handleDepositTransfer(IERC20Base token, address from, uint256 amount) internal {
    require(address(token) != address(0), ERR_INVALID_PRODUCT);
    uint256 before = token.balanceOf(address(clearinghouse));
    safeTransferFrom(token, from, amount);
    safeTransferTo(token, address(clearinghouse), amount);
    uint256 actualReceived = token.balanceOf(address(clearinghouse)) - before;
    // return actualReceived so the caller can encode it in the SlowModeTx
}
```

Alternatively, transfer directly from the user to the Clearinghouse in a single hop, eliminating the Endpoint as an intermediate custodian and removing the opportunity for balance leakage.

Document clearly that fee-on-transfer and rebasing tokens are not supported collateral types until this is resolved.

---

### Proof of Concept

**Setup**: Token `T` charges a 1% fee on every transfer. Token `T` is registered as a collateral product. The Endpoint holds 10 T (accumulated from slow-mode fees).

**Attack steps**:

1. Attacker calls `Endpoint.depositCollateral(subaccountName, productId, 1000e6)` with `amount = 1000e6`.
2. `handleDepositTransfer` executes:
   - `safeTransferFrom(T, attacker, 1000e6)` → Endpoint receives `990e6` (1% fee deducted). Endpoint total: `10 + 990 = 1000e6`.
   - `safeTransferTo(T, clearinghouse, 1000e6)` → Clearinghouse receives `990e6` (1% fee deducted again). Endpoint total: `0`.
3. `SlowModeTx` is queued with `amount = 1000e6`.
4. Sequencer executes the slow-mode tx; `Clearinghouse.depositCollateral` credits `1000e6 * multiplier` to the attacker's subaccount.
5. **Result**: Clearinghouse holds `990e6` T but has credited `1000e6 * multiplier` — a `10e6 * multiplier` surplus. The Endpoint's pre-existing 10 T balance has been silently consumed.

Repeated deposits progressively widen the gap between the Clearinghouse's real balance and the sum of all credited subaccount balances, eventually preventing solvent withdrawals for other users. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** core/contracts/EndpointStorage.sol (L83-93)
```text
    function chargeSlowModeFee(IERC20Base token, address from)
        internal
        virtual
    {
        require(address(token) != address(0));
        token.safeTransferFrom(
            from,
            address(this),
            clearinghouse.getSlowModeFee()
        );
    }
```

**File:** core/contracts/EndpointStorage.sol (L111-119)
```text
    function handleDepositTransfer(
        IERC20Base token,
        address from,
        uint256 amount
    ) internal {
        require(address(token) != address(0), ERR_INVALID_PRODUCT);
        safeTransferFrom(token, from, amount);
        safeTransferTo(token, address(clearinghouse), amount);
    }
```

**File:** core/contracts/Endpoint.sol (L144-165)
```text
        handleDepositTransfer(
            IERC20Base(spotEngine.getToken(productId)),
            msg.sender,
            uint256(amount)
        );
        // copy from submitSlowModeTransaction
        SlowModeConfig memory _slowModeConfig = slowModeConfig;

        slowModeTxs[_slowModeConfig.txCount++] = SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: abi.encodePacked(
                uint8(TransactionType.DepositCollateral),
                abi.encode(
                    DepositCollateral({
                        sender: subaccount,
                        productId: productId,
                        amount: amount
                    })
                )
            )
        });
```

**File:** core/contracts/Clearinghouse.sol (L193-208)
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
```

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
