### Title
Slow-Mode `WithdrawCollateral` Path Skips `withdrawFeeX18` Fee, Causing Protocol Fee Loss — (File: `core/contracts/EndpointTx.sol`)

---

### Summary
The `processSlowModeTransactionImpl` handler for `WithdrawCollateral` does not charge the product-level `withdrawFeeX18` fee that is mandatory in the fast (sequencer) path. Any user can route a withdrawal through the slow-mode queue, pay only the flat `SLOW_MODE_FEE`, and completely bypass the percentage-based withdrawal fee that would otherwise accrue to the sequencer/protocol.

---

### Finding Description

`EndpointTx` processes `WithdrawCollateral` in two distinct code paths.

**Fast path** (`processTransactionImpl`, lines 413–436):

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    true
);                                          // internally calls requireSubaccount()
chargeFee(
    signedTx.tx.sender,
    spotEngine.getConfig(signedTx.tx.productId).withdrawFeeX18,
    signedTx.tx.productId
);                                          // percentage-based withdrawal fee
clearinghouse.withdrawCollateral(...);
``` [1](#0-0) 

**Slow-mode path** (`processSlowModeTransactionImpl`, lines 217–229):

```solidity
validateSender(txn.sender, sender);         // address-only check, no requireSubaccount
// ← NO chargeFee(withdrawFeeX18) here
clearinghouse.withdrawCollateral(
    txn.sender,
    txn.productId,
    txn.amount,
    address(0),
    nSubmissions
);
``` [2](#0-1) 

The slow-mode submission gate (`submitSlowModeTransactionImpl`) does not restrict `WithdrawCollateral` — it falls into the `else` branch that only charges the flat `SLOW_MODE_FEE` and queues the transaction: [3](#0-2) 

After the 3-day delay, `executeSlowModeTransaction` calls `processSlowModeTransactionImpl`, which executes the withdrawal without ever charging `withdrawFeeX18`. [4](#0-3) 

The `withdrawFeeX18` is a per-product, percentage-based fee stored in the spot engine config. It is charged in the fast path and credited to `sequencerFee[productId]`, which is later claimed by the protocol via `DumpFees`/`claimSequencerFees`. [5](#0-4) 

---

### Impact Explanation

For any withdrawal of size `A` with configured `withdrawFeeX18 = F`, the protocol loses `A × F` in fee revenue per bypassed withdrawal. A user withdrawing a large collateral position (e.g., $500k USDC at a 0.1% fee) would owe $500 in fees via the fast path but pays $0 via slow mode (only the flat slow-mode fee). The `sequencerFee` mapping is never incremented, so the fee is permanently lost — it cannot be recovered retroactively. [6](#0-5) 

---

### Likelihood Explanation

The slow-mode path is a publicly accessible, permissionless entry point — any user can call `submitSlowModeTransaction` with a `WithdrawCollateral` payload. The only cost is the flat `SLOW_MODE_FEE` and a 3-day wait. For any withdrawal where `amount × withdrawFeeX18 > SLOW_MODE_FEE`, the user has a direct financial incentive to use slow mode. This is trivially reachable by any depositor with a balance. [7](#0-6) 

---

### Recommendation

Add the `withdrawFeeX18` fee charge to the slow-mode `WithdrawCollateral` handler, mirroring the fast path:

```solidity
} else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
    IEndpoint.WithdrawCollateral memory txn = abi.decode(
        transaction[1:],
        (IEndpoint.WithdrawCollateral)
    );
    validateSender(txn.sender, sender);
    requireSubaccount(txn.sender);                          // add: prerequisite check
    chargeFee(                                              // add: fee parity with fast path
        txn.sender,
        spotEngine.getConfig(txn.productId).withdrawFeeX18,
        txn.productId
    );
    clearinghouse.withdrawCollateral(
        txn.sender,
        txn.productId,
        txn.amount,
        address(0),
        nSubmissions
    );
```

---

### Proof of Concept

1. Alice has 500,000 USDC deposited. The configured `withdrawFeeX18` for the USDC product is `1e15` (0.1%). Fast-path withdrawal fee = $500.
2. Alice calls `submitSlowModeTransaction` with a `WithdrawCollateral` payload for 500,000 USDC. She pays only the flat `SLOW_MODE_FEE` (a few dollars).
3. After 3 days, anyone calls `executeSlowModeTransaction`.
4. `processSlowModeTransactionImpl` handles the `WithdrawCollateral` case: calls `validateSender` (passes), then directly calls `clearinghouse.withdrawCollateral` — no `chargeFee` is invoked.
5. Alice receives her full 500,000 USDC. `sequencerFee[productId]` is never incremented. The protocol loses $500 in fee revenue. [2](#0-1) [8](#0-7)

### Citations

**File:** core/contracts/EndpointTx.sol (L130-141)
```text
    function chargeFee(bytes32 sender, int128 fee) internal {
        chargeFee(sender, fee, QUOTE_PRODUCT_ID);
    }

    function chargeFee(
        bytes32 sender,
        int128 fee,
        uint32 productId
    ) internal {
        spotEngine.updateBalance(productId, sender, -fee);
        sequencerFee[productId] += fee;
    }
```

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

**File:** core/contracts/EndpointTx.sol (L355-372)
```text
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
```

**File:** core/contracts/EndpointTx.sol (L413-436)
```text
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
            IEndpoint.SignedWithdrawCollateral memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedWithdrawCollateral)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            chargeFee(
                signedTx.tx.sender,
                spotEngine.getConfig(signedTx.tx.productId).withdrawFeeX18,
                signedTx.tx.productId
            );
            clearinghouse.withdrawCollateral(
                signedTx.tx.sender,
                signedTx.tx.productId,
                signedTx.tx.amount,
                address(0),
                nSubmissions
            );
```

**File:** core/contracts/Endpoint.sol (L173-183)
```text
    function submitSlowModeTransaction(bytes calldata transaction)
        external
        virtual
    {
        _delegatecallEndpointTx(
            abi.encodeWithSelector(
                EndpointTx.submitSlowModeTransactionImpl.selector,
                transaction
            )
        );
    }
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
