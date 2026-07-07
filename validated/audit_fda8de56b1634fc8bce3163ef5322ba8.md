### Title
Sanctioned Address Can Complete Slow-Mode Withdrawal After Being Sanctioned Post-Submission - (File: `core/contracts/EndpointTx.sol`)

---

### Summary
The slow-mode withdrawal path in Nado enforces a sanctions check only at **submission time**, not at **execution time**. A user who submits a `WithdrawCollateral` slow-mode transaction while unsanctioned, then gets sanctioned during the mandatory 3-day delay, will have their withdrawal executed without any sanctions re-validation — directly analogous to the external report's one-sided restriction check.

---

### Finding Description

In `submitSlowModeTransactionImpl`, a `requireUnsanctioned(sender)` check is performed on `msg.sender` before the transaction is queued: [1](#0-0) 

However, when the queued transaction is later executed via `processSlowModeTransactionImpl`, the `WithdrawCollateral` branch performs only a `validateSender` ownership check and immediately calls `clearinghouse.withdrawCollateral` — with **no sanctions re-check** on the sender: [2](#0-1) 

Compare this with the deposit path in `depositCollateralWithReferral`, which checks sanctions on **both** `msg.sender` and the subaccount owner address at the time of the actual fund movement: [3](#0-2) 

The `requireUnsanctioned` helper itself is straightforward — it queries the external sanctions oracle: [4](#0-3) 

The slow-mode delay is hardcoded to 3 days (`SLOW_MODE_TX_DELAY`): [5](#0-4) 

This 3-day window is precisely the gap in which a user can be sanctioned after submission but before execution.

---

### Impact Explanation

A sanctioned address successfully withdraws collateral from the Nado protocol. The sanctions enforcement mechanism — which is the only on-chain control preventing sanctioned users from moving funds — is bypassed entirely for the slow-mode withdrawal path. The withdrawn tokens (any supported collateral asset) leave the clearinghouse and are transferred to the sanctioned address via `WithdrawPool`, constituting a direct asset movement in violation of the protocol's compliance invariant. [6](#0-5) 

---

### Likelihood Explanation

The slow-mode path is a permissionless, user-accessible entrypoint — no sequencer or admin involvement is required to submit or execute a slow-mode transaction. The 3-day mandatory delay (`SLOW_MODE_TX_DELAY`) creates a realistic window during which a user can be added to a sanctions list (e.g., OFAC SDN list propagated to Chainalysis oracle). Once the delay expires, `executeSlowModeTransaction()` can be called by anyone, including the sanctioned user themselves, to finalize the withdrawal. [7](#0-6) 

---

### Recommendation

Add a `requireUnsanctioned` check on the sender at **execution time** inside `processSlowModeTransactionImpl` for the `WithdrawCollateral` branch, mirroring the deposit path's dual-check pattern:

```solidity
} else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
    IEndpoint.WithdrawCollateral memory txn = abi.decode(
        transaction[1:],
        (IEndpoint.WithdrawCollateral)
    );
    validateSender(txn.sender, sender);
    requireUnsanctioned(sender); // <-- add this
    clearinghouse.withdrawCollateral(
        txn.sender,
        txn.productId,
        txn.amount,
        address(0),
        nSubmissions
    );
}
``` [2](#0-1) 

---

### Proof of Concept

1. Alice holds collateral in Nado and is not currently sanctioned.
2. Alice calls `submitSlowModeTransaction` with a `WithdrawCollateral` payload. The call passes `requireUnsanctioned(msg.sender)` at line 375 and the transaction is queued with `executableAt = block.timestamp + SLOW_MODE_TX_DELAY`.
3. Within the 3-day window, Alice's address is added to the Chainalysis sanctions oracle (e.g., OFAC SDN designation).
4. After `SLOW_MODE_TX_DELAY` elapses, anyone calls `executeSlowModeTransaction()`.
5. `processSlowModeTransactionImpl` is invoked. The `WithdrawCollateral` branch calls only `validateSender` (passes, since Alice is the sender) and then `clearinghouse.withdrawCollateral` — no sanctions check occurs.
6. Alice's collateral is transferred to her address via `WithdrawPool.submitWithdrawal`, despite her sanctioned status. [8](#0-7) [9](#0-8)

### Citations

**File:** core/contracts/EndpointTx.sol (L217-229)
```text
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
            IEndpoint.WithdrawCollateral memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.WithdrawCollateral)
            );
            validateSender(txn.sender, sender);
            clearinghouse.withdrawCollateral(
                txn.sender,
                txn.productId,
                txn.amount,
                address(0),
                nSubmissions
            );
```

**File:** core/contracts/EndpointTx.sol (L332-385)
```text
    function submitSlowModeTransactionImpl(bytes calldata transaction) public {
        IEndpoint.TransactionType txType = IEndpoint.TransactionType(
            uint8(transaction[0])
        );

        // special case for DepositCollateral because upon
        // slow mode submission we must take custody of the
        // actual funds

        address sender = msg.sender;

        if (txType == IEndpoint.TransactionType.DepositCollateral) {
            revert();
        } else if (txType == IEndpoint.TransactionType.DepositInsurance) {
            IEndpoint.DepositInsurance memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.DepositInsurance)
            );
            require(
                txn.amount >= uint128(SLOW_MODE_FEE),
                ERR_DEPOSIT_TOO_SMALL
            );
            handleDepositTransfer(_getQuote(), sender, uint256(txn.amount));
        } else if (
            txType == IEndpoint.TransactionType.WithdrawInsurance ||
            txType == IEndpoint.TransactionType.DelistProduct ||
            txType == IEndpoint.TransactionType.DumpFees ||
            txType == IEndpoint.TransactionType.RebalanceXWithdraw ||
            txType == IEndpoint.TransactionType.UpdateTierFeeRates ||
            txType == IEndpoint.TransactionType.AddNlpPool ||
            txType == IEndpoint.TransactionType.UpdateNlpPool ||
            txType == IEndpoint.TransactionType.DeleteNlpPool ||
            txType == IEndpoint.TransactionType.ForceRebalanceNlpPool ||
            txType == IEndpoint.TransactionType.NlpProfitShare ||
            txType == IEndpoint.TransactionType.UpdateBuilder
        ) {
            require(sender == owner());
        } else {
            chargeSlowModeFee(_getQuote(), sender);
            slowModeFees += SLOW_MODE_FEE;
        }

        IEndpoint.SlowModeConfig memory _slowModeConfig = slowModeConfig;
        requireUnsanctioned(sender);
        slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: transaction
        });
        // TODO: to save on costs we could potentially just emit something
        // for now, we can just create a separate loop in the engine that queries the remote
        // sequencer for slow mode transactions, and ignore the possibility of a reorgy attack
        slowModeConfig = _slowModeConfig;
    }
```

**File:** core/contracts/Endpoint.sol (L133-135)
```text
        // depositor / depositee need to be unsanctioned
        requireUnsanctioned(msg.sender);
        requireUnsanctioned(sender);
```

**File:** core/contracts/Endpoint.sol (L152-153)
```text
        slowModeTxs[_slowModeConfig.txCount++] = SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
```

**File:** core/contracts/Endpoint.sol (L231-236)
```text
    function executeSlowModeTransaction() external {
        SlowModeConfig memory _slowModeConfig = slowModeConfig;
        _executeSlowModeTransaction(_slowModeConfig, false);
        nSubmissions += 1;
        slowModeConfig = _slowModeConfig;
    }
```

**File:** core/contracts/EndpointStorage.sol (L121-123)
```text
    function requireUnsanctioned(address sender) internal view virtual {
        require(!sanctions.isSanctioned(sender), ERR_WALLET_SANCTIONED);
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
