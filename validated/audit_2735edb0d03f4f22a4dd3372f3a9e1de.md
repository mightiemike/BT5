### Title
Deposit Internal Balance Overcrediting via Fee-on-Transfer Token — (`core/contracts/Endpoint.sol`, `core/contracts/Clearinghouse.sol`, `core/contracts/EndpointStorage.sol`)

---

### Summary

Nado's deposit flow records the caller-supplied `amount` parameter into a slow-mode transaction and later credits that same `amount` to the subaccount's internal balance, without ever verifying the actual token quantity received by the Clearinghouse. For any ERC20 token that deducts a fee on `transfer` (but not on `transferFrom`), the Clearinghouse receives fewer tokens than the amount credited, inflating the depositor's internal balance and allowing them to withdraw or trade against phantom collateral.

---

### Finding Description

The deposit entry point is `depositCollateralWithReferral` in `Endpoint.sol`:

```
handleDepositTransfer(token, msg.sender, uint256(amount));   // (1) physical transfer
...
slowModeTxs[...] = SlowModeTx({
    tx: abi.encodePacked(
        uint8(TransactionType.DepositCollateral),
        abi.encode(DepositCollateral({ sender: subaccount, productId: productId, amount: amount }))
    )
});
```

`handleDepositTransfer` in `EndpointStorage.sol` performs two hops:

```
safeTransferFrom(token, from, amount);              // user → Endpoint
safeTransferTo(token, address(clearinghouse), amount); // Endpoint → Clearinghouse
```

When the slow-mode transaction is later executed, `Clearinghouse.depositCollateral` credits the subaccount using the original `amount` field:

```
int128 amountRealized = int128(txn.amount) * int128(multiplier);
spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
```

No balance-before/after check is performed at any point. The `amount` encoded in the slow-mode transaction is the caller-controlled input, not the actual tokens received.

**Exploitable condition:** For a token that charges a fee only on `transfer` (not `transferFrom`):

- `safeTransferFrom(token, user, amount)` → Endpoint receives the full `amount` (no fee on `transferFrom`)
- `safeTransferTo(token, clearinghouse, amount)` → Clearinghouse receives `amount − fee` (fee deducted on `transfer`)
- Slow-mode tx records `amount`; `depositCollateral` credits `amountRealized` based on `amount`

The Clearinghouse holds `amount − fee` tokens but the subaccount is credited `amount`. The gap is phantom collateral.

For standard symmetric fee-on-transfer tokens (fee on both `transferFrom` and `transfer`), the second hop in `handleDepositTransfer` would revert because the Endpoint only received `amount − fee1` in the first hop. However, this does not eliminate the root cause; it only narrows the token surface. Upgradeable tokens (e.g., USDC) could introduce asymmetric fee logic post-deployment, and the protocol has no guard against it.

---

### Impact Explanation

A depositor using a qualifying token is credited more internal balance than the Clearinghouse actually holds. This phantom balance:

- Can be used as collateral to open leveraged perp positions
- Can be withdrawn (up to the `assertUtilization` check, which compares `totalDeposits` vs `totalBorrows` — both of which are inflated by the phantom credit)
- Dilutes yield for all other depositors of the same product, since `totalDepositsNormalized` is inflated

The corrupted state is `SpotEngine.balances[productId][subaccount].amountNormalized` and `SpotEngine.states[productId].totalDepositsNormalized`.

---

### Likelihood Explanation

- The protocol accepts arbitrary ERC20 tokens per `productId` configuration.
- Several widely-used tokens (USDC, USDT) are upgradeable and could introduce transfer fees.
- The asymmetric fee condition (fee on `transfer` only) is a realistic token design.
- No admin action is required; any depositor can trigger this by depositing a qualifying token.

---

### Recommendation

Replace the fixed-`amount` bookkeeping with an actual-balance-delta check. In `handleDepositTransfer`, measure the Clearinghouse's balance before and after the transfer and return the delta:

```solidity
function handleDepositTransfer(IERC20Base token, address from, uint256 amount)
    internal returns (uint256 actualReceived)
{
    require(address(token) != address(0), ERR_INVALID_PRODUCT);
    uint256 before = token.balanceOf(address(clearinghouse));
    safeTransferFrom(token, from, amount);
    safeTransferTo(token, address(clearinghouse), amount);
    actualReceived = token.balanceOf(address(clearinghouse)) - before;
}
```

Then encode `actualReceived` (scaled back to the token's native decimals) into the `DepositCollateral` slow-mode transaction instead of the raw `amount`.

---

### Proof of Concept

1. Deploy or configure a product with a token that charges a 1% fee on `transfer` but not on `transferFrom`.
2. Call `Endpoint.depositCollateralWithReferral(subaccount, productId, 1000e6, "")`.
3. `handleDepositTransfer` executes:
   - `safeTransferFrom(token, user, 1000e6)` → Endpoint receives 1000e6 (no fee).
   - `safeTransferTo(token, clearinghouse, 1000e6)` → Clearinghouse receives 990e6 (1% fee deducted).
4. Slow-mode tx is queued with `amount = 1000e6`.
5. After the delay, the sequencer executes the slow-mode tx; `Clearinghouse.depositCollateral` runs:
   - `amountRealized = 1000e6 * multiplier`
   - `spotEngine.updateBalance(productId, subaccount, amountRealized)` → subaccount credited 1000e6 worth.
6. Subaccount now holds 1000e6 of internal balance backed by only 990e6 of real tokens.
7. Repeat to accumulate phantom collateral; use it to open positions or withdraw, draining the pool.

---

**Root cause lines:** [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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

**File:** core/contracts/Clearinghouse.sol (L199-208)
```text
        require(txn.amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        ISpotEngine spotEngine = _spotEngine();
        uint8 decimals = _decimals(txn.productId);

        require(decimals <= MAX_DECIMALS);
        int256 multiplier = int256(10**(MAX_DECIMALS - decimals));
        int128 amountRealized = int128(txn.amount) * int128(multiplier);

        spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
        emit ModifyCollateral(amountRealized, txn.sender, txn.productId);
```
