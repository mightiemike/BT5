### Title
Unbounded `transaction` Payload in `submitSlowModeTransactionImpl` Permanently Blocks the Slow-Mode Queue — (`core/contracts/EndpointTx.sol`)

---

### Summary

`Endpoint.submitSlowModeTransaction` accepts a `bytes calldata transaction` of arbitrary size with no length cap. An attacker can submit a single oversized payload that, when the sequencer later tries to execute it, causes an out-of-gas (OOG) condition inside the try-catch guard. The OOG guard then calls `invalid()`, reverting the entire outer frame including the `txUpTo++` increment. Because the queue is strictly sequential, every subsequent slow-mode transaction — including legitimate user withdrawals — is permanently frozen.

---

### Finding Description

`submitSlowModeTransactionImpl` in `EndpointTx.sol` stores the raw calldata bytes directly into the `slowModeTxs` mapping with no size validation:

```solidity
// EndpointTx.sol L376-380
slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
    executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY,
    sender: sender,
    tx: transaction   // ← raw calldata, no length check
});
``` [1](#0-0) 

The only pre-storage checks are a sanctions screen and a small `SLOW_MODE_FEE` charge — neither bounds the size of `transaction`. [2](#0-1) 

When the sequencer (or anyone after the delay) calls `_executeSlowModeTransaction`, the stored bytes are passed to `processSlowModeTransaction` inside a try-catch:

```solidity
// Endpoint.sol L205-224
uint256 gasRemaining = gasleft();
try this.processSlowModeTransaction(txn.sender, txn.tx) {} catch {
    if (gasleft() <= 250000 || gasleft() <= gasRemaining / 2) {
        assembly { invalid() }   // ← reverts the entire outer frame
    }
}
``` [3](#0-2) 

If the stored `tx` bytes are large enough to exhaust gas inside `processSlowModeTransaction`, the OOG heuristic fires and `invalid()` is executed. This reverts the outer call, including the `delete slowModeTxs[_slowModeConfig.txUpTo++]` line that advances the queue pointer. [4](#0-3) 

Because `txUpTo` is never incremented, the queue is permanently stuck at the poisoned index. Every subsequent slow-mode transaction — at indices `txUpTo+1`, `txUpTo+2`, … — can never be reached. [5](#0-4) 

The sequencer path (`ExecuteSlowMode` inside `submitTransactionsChecked`) calls the same `_executeSlowModeTransaction` and is equally blocked. [6](#0-5) 

---

### Impact Explanation

All slow-mode withdrawals queued after the poisoned entry are permanently frozen. Users who submitted `WithdrawCollateral` or `WithdrawCollateralV2` slow-mode transactions after the attacker's entry cannot retrieve their collateral. The `SlowModeTx.tx` field is a `bytes` storage variable, so the oversized payload is durably stored on-chain and cannot be removed without an owner-level upgrade. [7](#0-6) 

---

### Likelihood Explanation

Any unsanctioned address can call `Endpoint.submitSlowModeTransaction` permissionlessly. The only cost is the `SLOW_MODE_FEE` (a small fixed amount of quote token). The attacker does not need any special role, linked signer, or existing subaccount. The attack is a single transaction and is irreversible once the poisoned entry is ahead of any legitimate pending withdrawal. [8](#0-7) 

---

### Recommendation

Add a maximum payload size check at the top of `submitSlowModeTransactionImpl`, before the fee charge and storage write:

```solidity
// EndpointTx.sol — submitSlowModeTransactionImpl
function submitSlowModeTransactionImpl(bytes calldata transaction) public {
+   require(transaction.length <= MAX_SLOW_MODE_TX_SIZE, "tx too large");
    IEndpoint.TransactionType txType = IEndpoint.TransactionType(
        uint8(transaction[0])
    );
    ...
}
```

`MAX_SLOW_MODE_TX_SIZE` should be set to the largest legitimately encodable slow-mode transaction (e.g. 1 KB is more than sufficient for all current `TransactionType` variants, all of which decode into fixed-size structs). [9](#0-8) 

---

### Proof of Concept

1. Attacker calls `Endpoint.submitSlowModeTransaction` with a `transaction` argument of, say, 500 KB of arbitrary bytes (first byte set to a valid `TransactionType` such as `WithdrawCollateral`). The call succeeds after paying `SLOW_MODE_FEE`.
2. The poisoned entry is stored at index `N = slowModeConfig.txCount`.
3. Any legitimate user submits a `WithdrawCollateral` slow-mode transaction; it lands at index `N+1`.
4. After the 3-day delay, the sequencer (or the user) calls `executeSlowModeTransaction`. `_executeSlowModeTransaction` loads index `N`, calls `this.processSlowModeTransaction(sender, <500KB bytes>)`. The inner call runs out of gas decoding/copying the oversized payload.
5. The OOG heuristic (`gasleft() <= 250000 || gasleft() <= gasRemaining / 2`) triggers `invalid()`, reverting the outer frame. `txUpTo` remains at `N`.
6. Every subsequent call to `executeSlowModeTransaction` repeats step 4-5. Index `N+1` (the legitimate withdrawal) is permanently unreachable. [10](#0-9)

### Citations

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

**File:** core/contracts/Endpoint.sol (L185-229)
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
```

**File:** core/contracts/Endpoint.sol (L257-260)
```text
        if (txType == TransactionType.ExecuteSlowMode) {
            SlowModeConfig memory _slowModeConfig = slowModeConfig;
            _executeSlowModeTransaction(_slowModeConfig, true);
            slowModeConfig = _slowModeConfig;
```

**File:** core/contracts/interfaces/IEndpoint.sol (L297-301)
```text
    struct SlowModeTx {
        uint64 executableAt;
        address sender;
        bytes tx;
    }
```
