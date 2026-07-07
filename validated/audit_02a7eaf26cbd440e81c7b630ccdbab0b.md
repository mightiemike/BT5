### Title
Deposit Accounting Overcredits Users for Fee-on-Transfer Tokens, Enabling Order-Dependent Withdrawal Shortfalls - (File: `core/contracts/Endpoint.sol`, `core/contracts/Clearinghouse.sol`)

---

### Summary

`Endpoint.depositCollateralWithReferral()` records the user-supplied `amount` into the slow-mode transaction queue after pulling tokens, without measuring the actual tokens received. `Clearinghouse.depositCollateral()` then credits the full nominal `amount` to the user's `SpotEngine` balance. For any fee-on-transfer (deflationary) collateral token, the protocol systematically overcredits every depositor, making the sum of all internal balances exceed the actual token holdings. When users withdraw in sequence, earlier withdrawers drain the real token reserve, leaving later withdrawers unable to receive their full credited amount — an order-dependent, unfair loss identical in structure to the referenced bug.

---

### Finding Description

**Deposit path — nominal amount is recorded, not actual received amount:**

In `Endpoint.depositCollateralWithReferral()`, `handleDepositTransfer` pulls tokens from the caller using `safeTransferFrom(from, address(this), amount)`. For a fee-on-transfer token this delivers `amount × (1 − fee_rate)` to the Clearinghouse. Immediately after, the same nominal `amount` is written into the slow-mode transaction queue: [1](#0-0) 

When the sequencer later executes that slow-mode entry, `Clearinghouse.depositCollateral()` reads `txn.amount` — the original nominal value — and credits the full scaled amount to the user's `SpotEngine` balance: [2](#0-1) 

There is no post-transfer balance check anywhere in this path. `ERC20Helper.safeTransferFrom` only verifies the call did not revert; it does not compare pre/post balances: [3](#0-2) 

**Withdrawal path — two transfers, both using the nominal amount:**

`Clearinghouse.withdrawCollateral()` calls `handleWithdrawTransfer`, which first transfers `amount` tokens from the Clearinghouse to the `WithdrawPool`, then instructs the pool to forward `amount` to the user: [4](#0-3) 

`BaseWithdrawPool.handleWithdrawTransfer` executes the second leg with the same nominal `amount`: [5](#0-4) 

For a fee-on-transfer token each leg loses `fee_rate × amount`, so the user ultimately receives `amount × (1 − fee_rate)²` while their internal balance was debited by the full `amount`.

**Utilization check does not catch the discrepancy:**

`SpotEngine.assertUtilization()` only verifies `totalDeposits ≥ totalBorrows` in normalized units — it never compares internal accounting against the contract's actual ERC-20 balance: [6](#0-5) 

**Resulting invariant break:**

After N users each deposit `D` tokens of a fee-on-transfer token with rate `r`:
- Actual tokens held by Clearinghouse: `N × D × (1 − r)`
- Sum of internal `SpotEngine` balances: `N × D`
- Shortfall: `N × D × r`

The first withdrawers succeed by consuming tokens that belong to later depositors. The last withdrawers find the pool short and receive less than their credited balance, or the withdrawal reverts entirely.

---

### Impact Explanation

**Accounting corruption:** The `SpotEngine` normalized balance system (`totalDepositsNormalized × cumulativeDepositsMultiplierX18`) overstates the real token reserve for every fee-on-transfer collateral product. [7](#0-6) 

**Unfair, order-dependent loss:** Users who withdraw earlier receive their full credited amount at the expense of users who withdraw later. The loss is not shared proportionally — it falls entirely on the last withdrawers, which is the exact unfairness described in the reference report.

**Protocol insolvency for affected products:** The Clearinghouse holds less collateral than it owes. Any product configured with a fee-on-transfer token becomes permanently undercollateralized from the first deposit onward.

---

### Likelihood Explanation

The Nado `SpotEngine` is designed to support multiple collateral tokens added via `addOrUpdateProduct`. Any token whose `transfer`/`transferFrom` silently deducts a fee (e.g., tokens with built-in burn mechanics, reflection tokens, or tokens that add fees via an upgrade) triggers this path. The entry point — `depositCollateralWithReferral` — is publicly callable by any unsanctioned address with no special role required. No admin action is needed to exploit this once a fee-on-transfer token is listed as a supported collateral product. [8](#0-7) 

---

### Recommendation

After `handleDepositTransfer` executes, measure the actual tokens received by comparing the Clearinghouse's pre- and post-transfer balance, and record only the delta in the slow-mode transaction. For example:

```solidity
uint256 balanceBefore = token.balanceOf(address(clearinghouse));
handleDepositTransfer(token, msg.sender, uint256(amount));
uint256 actualReceived = token.balanceOf(address(clearinghouse)) - balanceBefore;
// use actualReceived (cast to uint128) in the DepositCollateral slow-mode tx
```

This ensures the credited amount always matches the actual tokens held, eliminating the overcredit regardless of token transfer mechanics.

---

### Proof of Concept

1. List a fee-on-transfer token (1% burn per transfer) as a spot collateral product via `addOrUpdateProduct`.
2. Alice calls `depositCollateralWithReferral(aliceSubaccount, productId, 1000e18, "")`.
   - Clearinghouse receives `990e18` tokens; Alice's slow-mode tx records `1000e18`.
3. Bob calls `depositCollateralWithReferral(bobSubaccount, productId, 1000e18, "")`.
   - Clearinghouse receives `990e18` more; Bob's slow-mode tx records `1000e18`.
4. Eve calls `depositCollateralWithReferral(eveSubaccount, productId, 1000e18, "")`.
   - Clearinghouse receives `990e18` more; Eve's slow-mode tx records `1000e18`.
5. Sequencer processes all three deposits. `SpotEngine` now shows: Alice = 1000e18, Bob = 1000e18, Eve = 1000e18. Actual Clearinghouse balance = `2970e18`.
6. Alice requests withdrawal of `1000e18`. `Clearinghouse.withdrawCollateral` transfers `1000e18` to WithdrawPool (pool receives `990e18`), then pool transfers `990e18` to Alice (Alice receives `980.1e18`). Alice's internal balance is debited `1000e18`.
7. Bob requests withdrawal of `1000e18`. Same path — Bob receives `980.1e18`. Clearinghouse now holds `970e18`.
8. Eve requests withdrawal of `1000e18`. Clearinghouse attempts to transfer `1000e18` to WithdrawPool but only holds `970e18` — the transfer reverts. Eve cannot withdraw at all, despite having the same credited balance as Alice and Bob.

Alice and Bob each lost `~19.9e18` tokens (two transfer fees). Eve loses her entire `1000e18` credited balance due to the revert, despite depositing the same amount. The loss is entirely order-dependent and unfair.

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

**File:** core/contracts/Clearinghouse.sol (L377-385)
```text
    function handleWithdrawTransfer(
        IERC20Base token,
        address to,
        uint128 amount,
        uint64 idx
    ) internal virtual {
        token.safeTransfer(withdrawPool, uint256(amount));
        BaseWithdrawPool(withdrawPool).submitWithdrawal(token, to, amount, idx);
    }
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

**File:** core/contracts/BaseWithdrawPool.sol (L184-190)
```text
    function handleWithdrawTransfer(
        IERC20Base token,
        address to,
        uint128 amount
    ) internal virtual {
        token.safeTransfer(to, uint256(amount));
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

**File:** core/contracts/SpotEngineState.sol (L15-50)
```text
    function _updateBalanceNormalized(
        State memory state,
        BalanceNormalized memory balance,
        int128 balanceDelta
    ) internal pure {
        if (balance.amountNormalized > 0) {
            state.totalDepositsNormalized -= balance.amountNormalized;
        } else {
            state.totalBorrowsNormalized += balance.amountNormalized;
        }

        int128 cumulativeMultiplierX18;
        if (balance.amountNormalized > 0) {
            cumulativeMultiplierX18 = state.cumulativeDepositsMultiplierX18;
        } else {
            cumulativeMultiplierX18 = state.cumulativeBorrowsMultiplierX18;
        }

        int128 newAmount = balance.amountNormalized.mul(
            cumulativeMultiplierX18
        ) + balanceDelta;

        if (newAmount > 0) {
            cumulativeMultiplierX18 = state.cumulativeDepositsMultiplierX18;
        } else {
            cumulativeMultiplierX18 = state.cumulativeBorrowsMultiplierX18;
        }

        balance.amountNormalized = newAmount.div(cumulativeMultiplierX18);

        if (balance.amountNormalized > 0) {
            state.totalDepositsNormalized += balance.amountNormalized;
        } else {
            state.totalBorrowsNormalized -= balance.amountNormalized;
        }
    }
```
