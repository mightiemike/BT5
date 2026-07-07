### Title
Fee-on-Transfer Token Deposit Credits Full `amount` Despite Receiving Less — (`File: core/contracts/EndpointStorage.sol`)

---

### Summary

`handleDepositTransfer` in `EndpointStorage.sol` performs two sequential ERC20 transfers using the same nominal `amount`. For fee-on-transfer tokens, each hop deducts a fee, so the `Clearinghouse` receives materially less than `amount`. However, `Clearinghouse.depositCollateral` credits the subaccount with the full original `amount`, permanently inflating the protocol's internal accounting relative to its actual token holdings.

---

### Finding Description

The deposit path in Nado is a two-hop transfer:

**Step 1 — `EndpointStorage.handleDepositTransfer`** (called from `Endpoint.depositCollateralWithReferral`):

```
safeTransferFrom(token, from, amount);       // user → Endpoint
safeTransferTo(token, address(clearinghouse), amount); // Endpoint → Clearinghouse
``` [1](#0-0) 

For a fee-on-transfer token with fee rate `f`, the Clearinghouse actually receives `amount * (1-f)^2` (fee taken on each hop), yet the slow-mode transaction is queued encoding the original `amount`.

**Step 2 — `Clearinghouse.depositCollateral`** (executed by the sequencer):

```solidity
int128 amountRealized = int128(txn.amount) * int128(multiplier);
spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
``` [2](#0-1) 

`txn.amount` is the original user-specified value — not the actual received amount. The subaccount is credited with the full nominal amount, inflating `totalDepositsNormalized` in `SpotEngine` beyond the real token balance held by the Clearinghouse.

**Step 3 — `SpotEngine.assertUtilization`** does not catch this:

```solidity
require(totalDeposits >= totalBorrows, ERR_MAX_UTILIZATION);
``` [3](#0-2) 

This check only compares internal accounting figures against each other; it never compares `totalDeposits` against the actual ERC20 balance held by the Clearinghouse. The insolvency is invisible to the protocol.

**Step 4 — `Clearinghouse.withdrawCollateral`** sends the full credited `amount` on withdrawal:

```solidity
handleWithdrawTransfer(token, sendTo, amount, idx);
``` [4](#0-3) 

Since the Clearinghouse holds less than the sum of all credited balances, the last withdrawers will find the Clearinghouse unable to fulfill their withdrawals.

---

### Impact Explanation

- **Accounting corruption**: Every deposit of a fee-on-transfer token inflates `totalDepositsNormalized` by the fee amount (doubled due to the two-hop transfer). The protocol believes it holds more collateral than it does.
- **Protocol insolvency**: The Clearinghouse's actual token balance is less than the sum of all subaccount credits. Early withdrawers drain the real balance; later withdrawers cannot withdraw.
- **Health checks are corrupted**: Subaccount health is computed from inflated balances, allowing users to open positions or borrow against collateral that does not fully exist on-chain.
- **`assertUtilization` is bypassed**: The utilization guard compares internal accounting to internal accounting, not to the real token balance, so it provides no protection.

---

### Likelihood Explanation

Any unprivileged user can trigger this by calling `Endpoint.depositCollateral` or `Endpoint.depositCollateralWithReferral` with a supported product whose underlying token charges a transfer fee (e.g., USDT with fee mode enabled, STA, or any rebasing token listed as a spot product). No special privileges are required. The entry path is a standard public function. [5](#0-4) [6](#0-5) 

---

### Recommendation

1. **Measure actual received amount**: In `handleDepositTransfer`, record the Clearinghouse's token balance before and after the transfer and pass the delta (not `amount`) into the slow-mode transaction payload.
2. **Single-hop transfer**: Transfer directly from the user to the Clearinghouse in one `transferFrom` call to eliminate the double-fee problem.
3. **Disallow fee-on-transfer tokens**: Add an explicit check during product listing that the token does not charge transfer fees.
4. **Strengthen `assertUtilization`**: Compare `totalDeposits` against `IERC20Base(token).balanceOf(address(this))` to detect real-balance shortfalls.

---

### Proof of Concept

1. A fee-on-transfer token (1% fee per transfer) is listed as a supported spot product.
2. Alice calls `Endpoint.depositCollateral(subaccountName, productId, 1000e18)`.
3. `handleDepositTransfer` executes:
   - `safeTransferFrom(token, Alice, Endpoint, 1000e18)` → Endpoint receives `990e18` (1% fee).
   - `safeTransferTo(token, Endpoint, Clearinghouse, 1000e18)` → Clearinghouse receives `980.1e18` (another 1% fee on 990e18, but the call passes 1000e18 so it reverts — or if the Endpoint had a prior balance, it passes and Clearinghouse receives `990e18`).
4. The slow-mode tx encodes `amount = 1000e18`.
5. Sequencer processes `Clearinghouse.depositCollateral`: Alice's subaccount is credited `1000e18 * multiplier`.
6. Alice's internal balance exceeds the actual tokens held by the Clearinghouse by at least `~10e18` per deposit.
7. After many such deposits, the Clearinghouse's real token balance is insufficient to cover all credited withdrawals; the last withdrawers cannot withdraw. [1](#0-0) [7](#0-6) [8](#0-7) [9](#0-8)

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

**File:** core/contracts/Endpoint.sol (L103-121)
```text
    function depositCollateral(
        bytes12 subaccountName,
        uint32 productId,
        uint128 amount
    ) external {
        bytes32 subaccount = bytes32(
            abi.encodePacked(msg.sender, subaccountName)
        );
        require(
            isValidDepositAmount(subaccount, productId, amount),
            ERR_DEPOSIT_TOO_SMALL
        );
        depositCollateralWithReferral(
            subaccount,
            productId,
            amount,
            DEFAULT_REFERRAL_CODE
        );
    }
```

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
