### Title
Fee-on-Transfer Token Accounting Corruption in `depositCollateralWithReferral` - (File: `core/contracts/EndpointStorage.sol`, `core/contracts/Endpoint.sol`)

---

### Summary
`EndpointStorage.handleDepositTransfer()` calls `safeTransferFrom` with a user-supplied `amount`, then immediately forwards that same `amount` to the clearinghouse via `safeTransferTo`. If the deposited token charges a transfer fee (e.g., USDT with fees enabled), the Endpoint contract receives less than `amount` from the user, yet attempts to forward the full `amount` to the clearinghouse — either draining pre-existing contract token balance or reverting. Independently, the slow-mode transaction queued in `depositCollateralWithReferral` records the original `amount` parameter, not the actual received amount, so when the sequencer processes the deposit, the user is credited more collateral than was actually received.

---

### Finding Description

The deposit flow in Nado proceeds as follows:

**Step 1 — User calls `Endpoint.depositCollateralWithReferral()`:**

```solidity
// Endpoint.sol:144-165
handleDepositTransfer(
    IERC20Base(spotEngine.getToken(productId)),
    msg.sender,
    uint256(amount)                          // user-supplied amount
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
                amount: amount               // original amount recorded, not actual received
            })
        )
    )
});
``` [1](#0-0) 

**Step 2 — `handleDepositTransfer` in `EndpointStorage`:**

```solidity
// EndpointStorage.sol:111-119
function handleDepositTransfer(
    IERC20Base token,
    address from,
    uint256 amount
) internal {
    require(address(token) != address(0), ERR_INVALID_PRODUCT);
    safeTransferFrom(token, from, amount);                    // receives amount - fee
    safeTransferTo(token, address(clearinghouse), amount);    // forwards full amount
}
``` [2](#0-1) 

For a fee-on-transfer token:
- `safeTransferFrom(token, from, amount)` → Endpoint contract receives `amount - fee`
- `safeTransferTo(token, address(clearinghouse), amount)` → attempts to send `amount`, but only holds `amount - fee`

If the Endpoint contract holds a prior balance of that token (from previous deposits or any other source), the second call silently drains those pre-existing funds to cover the shortfall. If no prior balance exists, the call reverts.

**Step 3 — Sequencer processes the slow-mode tx via `Clearinghouse.depositCollateral()`:**

```solidity
// Clearinghouse.sol:199-208
require(txn.amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
int256 multiplier = int256(10**(MAX_DECIMALS - decimals));
int128 amountRealized = int128(txn.amount) * int128(multiplier);
spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
``` [3](#0-2) 

`txn.amount` is the original user-supplied `amount`, not the actual received amount. The user is credited the full `amount` in the spot engine, while the clearinghouse physically holds `amount - fee` (or `amount - 2*fee` if the second transfer also incurs a fee).

---

### Impact Explanation

The clearinghouse's on-chain token balance becomes less than the sum of all credited collateral positions. Each deposit with a fee-on-transfer token widens this gap. Downstream effects include:

- **Solvency corruption**: The protocol's backing assets are systematically less than the sum of credited balances. Withdrawals processed later will fail or drain funds belonging to other depositors.
- **Balance sheet inflation**: Users receive collateral credit exceeding their actual contribution, enabling them to open larger positions than the protocol can cover.
- **Silent fund drain**: If the Endpoint contract holds prior token balance (e.g., from slow-mode fees or other deposits in flight), those funds are silently consumed to cover the shortfall in `safeTransferTo`.

---

### Likelihood Explanation

USDT on Ethereum mainnet has a fee mechanism that is currently set to zero but can be activated by the USDT owner at any time without notice. Any token with a non-zero transfer fee that is listed as a supported spot product triggers this bug immediately upon any user deposit. The entry path requires no special privileges — any user calling `depositCollateral` or `depositCollateralWithReferral` with a fee-on-transfer token is sufficient.

---

### Recommendation

In `handleDepositTransfer`, measure the actual received amount by comparing balances before and after the `safeTransferFrom`, and use that measured amount for both the `safeTransferTo` and the slow-mode transaction record:

```solidity
function handleDepositTransfer(
    IERC20Base token,
    address from,
    uint256 amount
) internal returns (uint256 actualReceived) {
    require(address(token) != address(0), ERR_INVALID_PRODUCT);
    uint256 balanceBefore = token.balanceOf(address(this));
    safeTransferFrom(token, from, amount);
    actualReceived = token.balanceOf(address(this)) - balanceBefore;
    safeTransferTo(token, address(clearinghouse), actualReceived);
}
```

Then propagate `actualReceived` back to `depositCollateralWithReferral` so the slow-mode `DepositCollateral` transaction records the true deposited amount instead of the user-supplied `amount`.

---

### Proof of Concept

1. A fee-on-transfer token (e.g., USDT with 1% fee) is listed as a supported spot product.
2. Alice calls `Endpoint.depositCollateral(subaccountName, productId, 1000e6)`.
3. `handleDepositTransfer` calls `safeTransferFrom(token, Alice, 1000e6)` → Endpoint receives `990e6`.
4. `handleDepositTransfer` calls `safeTransferTo(token, clearinghouse, 1000e6)` → if Endpoint has ≥ `10e6` prior balance, this succeeds and drains those funds; clearinghouse receives `990e6` (after another fee).
5. The slow-mode tx records `amount = 1000e6`.
6. Sequencer processes the tx: `Clearinghouse.depositCollateral` credits Alice with `1000e6` (scaled) in the spot engine.
7. Clearinghouse physically holds `≈980e6` but has credited `1000e6` — a `≈20e6` deficit per deposit cycle.
8. Repeated deposits compound the deficit until the clearinghouse cannot honor withdrawals. [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** core/contracts/Endpoint.sol (L123-167)
```text
    function depositCollateralWithReferral(
        bytes32 subaccount,
        uint32 productId,
        uint128 amount,
        string memory
    ) public {
        require(!RiskHelper.isIsolatedSubaccount(subaccount), ERR_UNAUTHORIZED);

        address sender = address(bytes20(subaccount));

        // depositor / depositee need to be unsanctioned
        requireUnsanctioned(msg.sender);
        requireUnsanctioned(sender);

        if (!isValidDepositAmount(subaccount, productId, amount)) {
            // we cannot revert here, otherwise direct deposit could be blocked when there are
            // multiple assets awaiting credit but one of them is below the minimum deposit amount.
            // we can just skip the deposit and continue with the next asset.
            return;
        }

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
        slowModeConfig = _slowModeConfig;
    }
```

**File:** core/contracts/EndpointStorage.sol (L95-119)
```text
    function safeTransferFrom(
        IERC20Base token,
        address from,
        uint256 amount
    ) internal virtual {
        token.safeTransferFrom(from, address(this), amount);
    }

    function safeTransferTo(
        IERC20Base token,
        address to,
        uint256 amount
    ) internal virtual {
        token.safeTransfer(to, amount);
    }

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

**File:** core/contracts/Clearinghouse.sol (L193-209)
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
    }
```
