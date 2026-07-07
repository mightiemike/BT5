### Title
Fee-on-Transfer Token Accounting Discrepancy in Deposit Flow Inflates Subaccount Balances Beyond Actual Holdings - (File: `core/contracts/EndpointStorage.sol`)

---

### Summary

`EndpointStorage.handleDepositTransfer` performs two sequential token transfers without any before/after balance check. When a fee-on-transfer (FoT) token is used, the actual amount received by `Clearinghouse` is less than the `amount` parameter. However, the original `amount` is encoded into the slow-mode `DepositCollateral` transaction and later credited to the subaccount in full by `Clearinghouse.depositCollateral`. This creates a permanent discrepancy between actual token holdings and recorded subaccount balances, leading to protocol insolvency for that token.

---

### Finding Description

The deposit flow proceeds as follows:

**Step 1 — `Endpoint.depositCollateralWithReferral`** calls `handleDepositTransfer` with the caller-supplied `amount`, then immediately encodes that same `amount` into the slow-mode queue:

```solidity
// Endpoint.sol lines 144–165
handleDepositTransfer(
    IERC20Base(spotEngine.getToken(productId)),
    msg.sender,
    uint256(amount)
);
// ...
slowModeTxs[_slowModeConfig.txCount++] = SlowModeTx({
    // ...
    tx: abi.encodePacked(
        uint8(TransactionType.DepositCollateral),
        abi.encode(
            DepositCollateral({
                sender: subaccount,
                productId: productId,
                amount: amount          // <-- original amount, not actual received
            })
        )
    )
});
``` [1](#0-0) 

**Step 2 — `EndpointStorage.handleDepositTransfer`** performs two transfers using the same `amount` with no balance reconciliation:

```solidity
// EndpointStorage.sol lines 111–119
function handleDepositTransfer(
    IERC20Base token,
    address from,
    uint256 amount
) internal {
    require(address(token) != address(0), ERR_INVALID_PRODUCT);
    safeTransferFrom(token, from, amount);          // user → Endpoint
    safeTransferTo(token, address(clearinghouse), amount); // Endpoint → Clearinghouse
}
``` [2](#0-1) 

There is no `balanceOf` check before or after either transfer. If the token charges a fee on every transfer, the Clearinghouse receives `amount * (1 - fee)` on the second transfer, while the Endpoint may drain tokens from prior depositors to cover the shortfall on the second transfer.

**Step 3 — `Clearinghouse.depositCollateral`** credits the subaccount using `txn.amount` — the original, unreconciled value:

```solidity
// Clearinghouse.sol lines 205–208
int128 amountRealized = int128(txn.amount) * int128(multiplier);
spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
``` [3](#0-2) 

The `ERC20Helper.safeTransferFrom` helper only checks for call success and return value; it does not measure actual received amounts: [4](#0-3) 

---

### Impact Explanation

For every deposit of a FoT token:
- The Clearinghouse's actual token balance is `amount * (1 - fee)^2` (two transfers, each incurring the fee).
- The subaccount's recorded balance is credited with the full `amount` (normalized).
- Over time, `totalDepositsNormalized * cumulativeDepositsMultiplierX18` in `SpotEngine` exceeds the real token balance held by Clearinghouse.
- `assertUtilization` only checks internal accounting invariants (`totalDeposits >= totalBorrows`), not actual token holdings, so it does not catch this drift.
- When users attempt to withdraw, the Clearinghouse will eventually be unable to fulfill withdrawals — the last withdrawers lose funds. The protocol becomes insolvent for that token. [5](#0-4) 

---

### Likelihood Explanation

`SpotEngine.addOrUpdateProduct` is `onlyOwner`, so a FoT token must be listed by the owner. However:
- Once listed, **every deposit** by any unprivileged user triggers the accounting corruption — no special attacker action is needed beyond a normal `depositCollateral` call.
- The `DirectDepositV1.creditDeposit` path also calls `depositCollateralWithReferral` with the DDA's actual token balance, which is equally affected. [6](#0-5) 

Likelihood is **Medium**: requires a FoT token to be listed, but the impact is automatic and affects all depositors once it is.

---

### Recommendation

In `handleDepositTransfer`, measure the actual amount received by Clearinghouse using before/after balance checks, and return the reconciled amount to the caller so it can be encoded into the slow-mode transaction:

```solidity
function handleDepositTransfer(
    IERC20Base token,
    address from,
    uint256 amount
) internal returns (uint256 actualReceived) {
    require(address(token) != address(0), ERR_INVALID_PRODUCT);
    safeTransferFrom(token, from, amount);
    uint256 before = token.balanceOf(address(clearinghouse));
    safeTransferTo(token, address(clearinghouse), amount);
    actualReceived = token.balanceOf(address(clearinghouse)) - before;
}
```

Then in `depositCollateralWithReferral`, use `actualReceived` (cast to `uint128`) when constructing the `DepositCollateral` struct instead of the original `amount`. Alternatively, explicitly document and enforce that FoT/rebasing tokens are not supported as spot products.

---

### Proof of Concept

1. Owner lists token `XYZ` (10% FoT) as a spot product via `SpotEngine.addOrUpdateProduct`.
2. Alice calls `Endpoint.depositCollateral(subaccountName, xyzProductId, 100e18)`.
3. `handleDepositTransfer` executes:
   - `safeTransferFrom(XYZ, Alice, Endpoint, 100e18)` → Endpoint receives `90e18` (10% fee taken).
   - `safeTransferTo(XYZ, Endpoint, Clearinghouse, 100e18)` → Clearinghouse receives `90e18` (another 10% fee), but Endpoint only had `90e18`, so this call attempts to transfer `100e18` and either reverts (if no buffer) or drains `10e18` from prior depositors' tokens held in Endpoint.
4. Assuming the transfer succeeds (Endpoint had a buffer), the slow-mode queue records `DepositCollateral { amount: 100e18 }`.
5. When the sequencer processes the slow-mode tx, `Clearinghouse.depositCollateral` credits Alice's subaccount with `100e18 * multiplier` units.
6. Clearinghouse actually holds only `90e18` XYZ for Alice's deposit.
7. Repeated deposits widen the gap. When Alice (or any user) withdraws, the Clearinghouse cannot cover the full recorded balance, causing insolvency.

### Citations

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

**File:** core/contracts/Clearinghouse.sol (L205-208)
```text
        int128 amountRealized = int128(txn.amount) * int128(multiplier);

        spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
        emit ModifyCollateral(amountRealized, txn.sender, txn.productId);
```

**File:** core/contracts/libraries/ERC20Helper.sol (L23-42)
```text
    function safeTransferFrom(
        IERC20Base self,
        address from,
        address to,
        uint256 amount
    ) internal {
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(
                IERC20Base.transferFrom.selector,
                from,
                to,
                amount
            )
        );

        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
    }
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
