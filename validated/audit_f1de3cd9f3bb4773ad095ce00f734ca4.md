### Title
Withdrawal Fee Bypassed via Slow Mode Path — (`File: core/contracts/EndpointTx.sol`)

---

### Summary

The Nado protocol charges a product-specific `withdrawFeeX18` when users withdraw collateral through the sequencer path (`processTransactionImpl`). However, the slow mode withdrawal path (`processSlowModeTransactionImpl`) processes the same `WithdrawCollateral` transaction type without charging this fee, allowing any user to bypass it entirely.

---

### Finding Description

There are two code paths that handle a `WithdrawCollateral` transaction:

**Path 1 — Sequencer path (`processTransactionImpl`, lines 413–436):**

```solidity
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
``` [1](#0-0) 

**Path 2 — Slow mode path (`processSlowModeTransactionImpl`, lines 217–229):**

```solidity
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
``` [2](#0-1) 

The slow mode path calls `clearinghouse.withdrawCollateral` directly with **no preceding `chargeFee` call** for `withdrawFeeX18`. The only cost imposed on the slow mode path is a flat `SLOW_MODE_FEE` charged in the quote token at submission time (`submitSlowModeTransactionImpl`, line 370), which is a separate, unrelated fee. [3](#0-2) 

---

### Impact Explanation

Any user withdrawing collateral via the slow mode path avoids paying the product-specific `withdrawFeeX18`. For non-quote tokens (e.g., BTC, ETH), the user pays only the flat quote-denominated `SLOW_MODE_FEE` and escapes the proportional withdrawal fee entirely. This directly reduces protocol fee revenue. The `withdrawFeeX18` is a per-product configurable fee stored in `SpotEngine` config and is the intended revenue mechanism for withdrawals. [4](#0-3) 

**Impact: Medium** — Protocol loses withdrawal fee revenue on every slow mode withdrawal. No user funds are at risk, but the fee invariant is broken.

---

### Likelihood Explanation

**Likelihood: High** — The slow mode path is a documented, permissionless censorship-resistance mechanism. Any user can call `submitSlowModeTransaction` with a `WithdrawCollateral` payload, wait the 3-day delay, and then call `executeSlowModeTransaction`. No special privileges are required. A rational user who is aware of the fee difference will always prefer the slow mode path for large withdrawals. [5](#0-4) [6](#0-5) 

---

### Recommendation

Add the `chargeFee` call for `withdrawFeeX18` inside `processSlowModeTransactionImpl` when handling `WithdrawCollateral`, mirroring the logic in `processTransactionImpl`:

```solidity
} else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
    IEndpoint.WithdrawCollateral memory txn = abi.decode(
        transaction[1:],
        (IEndpoint.WithdrawCollateral)
    );
    validateSender(txn.sender, sender);
+   chargeFee(
+       txn.sender,
+       spotEngine.getConfig(txn.productId).withdrawFeeX18,
+       txn.productId
+   );
    clearinghouse.withdrawCollateral(
        txn.sender,
        txn.productId,
        txn.amount,
        address(0),
        nSubmissions
    );
``` [2](#0-1) 

---

### Proof of Concept

1. User has a balance of product `X` (e.g., BTC, `productId = 2`) with a configured `withdrawFeeX18 = 5e15` (0.5%).
2. User calls `Endpoint.submitSlowModeTransaction` with a `WithdrawCollateral` transaction encoding `{sender: userSubaccount, productId: 2, amount: 1e8}`.
3. `submitSlowModeTransactionImpl` charges only the flat `SLOW_MODE_FEE` in quote and enqueues the transaction.
4. After 3 days, user (or anyone) calls `Endpoint.executeSlowModeTransaction`.
5. `processSlowModeTransactionImpl` decodes the `WithdrawCollateral` transaction and calls `clearinghouse.withdrawCollateral` directly — **no `chargeFee` for `withdrawFeeX18` is executed**.
6. User receives the full `amount` without the 0.5% withdrawal fee being deducted, bypassing the fee that would have been charged via the sequencer path. [7](#0-6) [8](#0-7)

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

**File:** core/contracts/EndpointTx.sol (L425-436)
```text
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

**File:** core/contracts/Endpoint.sol (L185-236)
```text
    function _executeSlowModeTransaction(
        SlowModeConfig memory _slowModeConfig,
        bool fromSequencer
    ) internal {
        require(
            _slowModeConfig.txUpTo < _slowModeConfig.txCount,
            ERR_NO_SLOW_MODE_TXS_REMAINING
        );
        SlowModeTx memory txn = slowModeTxs[_slowModeConfig.txUpTo];
        delete slowModeTxs[_slowModeConfig.txUpTo++];

        require(
            fromSequencer || (txn.executableAt <= block.timestamp),
            ERR_SLOW_TX_TOO_RECENT
        );

        if (block.chainid == 31337) {
            // for testing purposes, we don't fail silently when the chainId is hardhat's default.
            this.processSlowModeTransaction(txn.sender, txn.tx);
        } else {
            uint256 gasRemaining = gasleft();
            // solhint-disable-next-line no-empty-blocks
            try this.processSlowModeTransaction(txn.sender, txn.tx) {} catch {
                // we need to differentiate between a revert and an out of gas
                // the issue is that in evm every inner call only 63/64 of the
                // remaining gas in the outer frame is forwarded. as a result
                // the amount of gas left for execution is (63/64)**len(stack)
                // and you can get an out of gas while spending an arbitrarily
                // low amount of gas in the final frame. we use a heuristic
                // here that isn't perfect but covers our cases.
                // having gasleft() <= gasRemaining / 2 buys us 44 nested calls
                // before we miss out of gas errors; 1/2 ~= (63/64)**44
                // this is good enough for our purposes

                if (gasleft() <= 250000 || gasleft() <= gasRemaining / 2) {
                    // solhint-disable-next-line no-inline-assembly
                    assembly {
                        invalid()
                    }
                }

                // try return funds now removed
            }
        }
    }

    function executeSlowModeTransaction() external {
        SlowModeConfig memory _slowModeConfig = slowModeConfig;
        _executeSlowModeTransaction(_slowModeConfig, false);
        nSubmissions += 1;
        slowModeConfig = _slowModeConfig;
    }
```
