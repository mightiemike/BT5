### Title
Withdrawal Fee Unconditionally Skipped in Slow-Mode Execution Path — (`File: core/contracts/EndpointTx.sol`)

---

### Summary

`EndpointTx.processTransactionImpl` charges the product-specific withdrawal fee (`withdrawFeeX18`) before executing a `WithdrawCollateral` transaction. The parallel slow-mode path, `processSlowModeTransactionImpl`, processes the identical transaction type but omits the fee charge entirely. Because the slow-mode queue is open to any user, any depositor can route withdrawals through it to avoid paying the product-specific fee, corrupting the protocol's `sequencerFee` accounting at no additional cost beyond the flat `SLOW_MODE_FEE`.

---

### Finding Description

**Sequencer path** — `processTransactionImpl`, `WithdrawCollateral` branch:

```
chargeFee(
    signedTx.tx.sender,
    spotEngine.getConfig(signedTx.tx.productId).withdrawFeeX18,
    signedTx.tx.productId
);
clearinghouse.withdrawCollateral(...);
``` [1](#0-0) 

The `chargeFee` call debits `withdrawFeeX18` from the sender's spot balance and credits `sequencerFee[productId]`. [2](#0-1) 

**Slow-mode path** — `processSlowModeTransactionImpl`, `WithdrawCollateral` branch:

```
validateSender(txn.sender, sender);
clearinghouse.withdrawCollateral(
    txn.sender, txn.productId, txn.amount, address(0), nSubmissions
);
``` [3](#0-2) 

No `chargeFee` call exists. `clearinghouse.withdrawCollateral` itself does not charge any fee — it only performs the balance debit, utilization assertion, and health check. [4](#0-3) 

The slow-mode queue is open to any caller via `Endpoint.submitSlowModeTransaction`, which charges only the flat `SLOW_MODE_FEE` (not the per-product `withdrawFeeX18`). [5](#0-4) 

After the 3-day delay, any caller (including the original submitter) can execute the queued transaction via `Endpoint.executeSlowModeTransaction`. [6](#0-5) 

---

### Impact Explanation

`sequencerFee[productId]` is never incremented for slow-mode withdrawals, permanently under-counting protocol fee revenue for that product. For any product where `withdrawFeeX18 > SLOW_MODE_FEE`, a rational user strictly prefers the slow-mode path. At scale, this drains the fee pool that backs `claimSequencerFees`, which redistributes fees to the `X_ACCOUNT` and ultimately to the protocol treasury. [7](#0-6) 

The corrupted state variable is `sequencerFee[productId]` in `EndpointStorage`, which is never credited for slow-mode withdrawals. [8](#0-7) 

---

### Likelihood Explanation

The slow-mode path is unconditionally available to every depositor — no privilege, no special role, no sequencer cooperation required. The only cost is the flat `SLOW_MODE_FEE` and a 3-day wait. For any withdrawal large enough that `withdrawFeeX18 × amount > SLOW_MODE_FEE`, the bypass is economically rational. Large traders and arbitrageurs withdrawing significant collateral have a direct financial incentive to use this path systematically.

---

### Recommendation

Add the product-specific fee charge to the slow-mode `WithdrawCollateral` branch in `processSlowModeTransactionImpl`, mirroring the sequencer path:

```solidity
} else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
    IEndpoint.WithdrawCollateral memory txn = abi.decode(
        transaction[1:], (IEndpoint.WithdrawCollateral)
    );
    validateSender(txn.sender, sender);
+   chargeFee(
+       txn.sender,
+       spotEngine.getConfig(txn.productId).withdrawFeeX18,
+       txn.productId
+   );
    clearinghouse.withdrawCollateral(
        txn.sender, txn.productId, txn.amount, address(0), nSubmissions
    );
```

---

### Proof of Concept

1. User holds collateral in product `P` where `withdrawFeeX18 = 5e15` (0.5%) and `SLOW_MODE_FEE` is a small flat amount.
2. User calls `Endpoint.submitSlowModeTransaction` with a `WithdrawCollateral{sender, productId: P, amount: 1_000_000e18}` payload, paying only `SLOW_MODE_FEE`.
3. After 3 days, user calls `Endpoint.executeSlowModeTransaction`.
4. `processSlowModeTransactionImpl` decodes the transaction, calls `validateSender` (passes — `msg.sender` matches), then calls `clearinghouse.withdrawCollateral` directly.
5. No `chargeFee` is executed. `sequencerFee[P]` is not incremented. The user receives the full `1_000_000e18` withdrawal without paying the 0.5% fee.
6. Compared to the sequencer path, the user saves `5_000e18` in fees per withdrawal. `sequencerFee[P]` is permanently under-counted by that amount. [3](#0-2) [9](#0-8)

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

**File:** core/contracts/EndpointTx.sol (L369-372)
```text
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

**File:** core/contracts/Clearinghouse.sol (L569-615)
```text
    function claimSequencerFees(int128[] calldata fees)
        external
        virtual
        onlyEndpoint
    {
        ISpotEngine spotEngine = _spotEngine();
        IPerpEngine perpEngine = _perpEngine();

        uint32[] memory spotIds = spotEngine.getProductIds();
        uint32[] memory perpIds = perpEngine.getProductIds();

        for (uint256 i = 0; i < spotIds.length; i++) {
            ISpotEngine.Balance memory feeBalance = spotEngine.getBalance(
                spotIds[i],
                FEES_ACCOUNT
            );
            spotEngine.updateBalance(
                spotIds[i],
                X_ACCOUNT,
                fees[i] + feeBalance.amount
            );
            spotEngine.updateBalance(
                spotIds[i],
                FEES_ACCOUNT,
                -feeBalance.amount
            );
        }

        for (uint256 i = 0; i < perpIds.length; i++) {
            IPerpEngine.Balance memory feeBalance = perpEngine.getBalance(
                perpIds[i],
                FEES_ACCOUNT
            );
            perpEngine.updateBalance(
                perpIds[i],
                X_ACCOUNT,
                feeBalance.amount,
                feeBalance.vQuoteBalance
            );
            perpEngine.updateBalance(
                perpIds[i],
                FEES_ACCOUNT,
                -feeBalance.amount,
                -feeBalance.vQuoteBalance
            );
        }
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
