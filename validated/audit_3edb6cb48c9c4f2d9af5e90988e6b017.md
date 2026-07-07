### Title
Sanctioned User Bypasses Sanctions Check by Frontrunning Sanction with Slow-Mode Withdrawal — (`core/contracts/EndpointTx.sol`)

---

### Summary

The Nado protocol enforces a sanctions check (`requireUnsanctioned`) only at the time a slow-mode transaction is **submitted**, not at the time it is **executed**. A user who is about to be sanctioned can frontrun the sanction by submitting a `WithdrawCollateral` slow-mode transaction. After the mandatory 3-day delay, the withdrawal executes with no sanctions check, allowing the sanctioned user to successfully extract collateral.

---

### Finding Description

The sanctions check in `submitSlowModeTransactionImpl` runs at submission time: [1](#0-0) 

```solidity
requireUnsanctioned(sender);
slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
    executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY,
    sender: sender,
    tx: transaction
});
```

However, when the queued transaction is later executed — either by the sequencer via `ExecuteSlowMode` or by any caller via `executeSlowModeTransaction()` — the execution path calls `processSlowModeTransactionImpl`, which handles `WithdrawCollateral` with **no sanctions check**: [2](#0-1) 

```solidity
} else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
    IEndpoint.WithdrawCollateral memory txn = abi.decode(...);
    validateSender(txn.sender, sender);
    clearinghouse.withdrawCollateral(
        txn.sender, txn.productId, txn.amount, address(0), nSubmissions
    );
}
```

The `_executeSlowModeTransaction` function and `executeSlowModeTransaction` (callable by anyone) also contain no sanctions check: [3](#0-2) 

The `requireUnsanctioned` helper is defined in `EndpointStorage`: [4](#0-3) 

---

### Impact Explanation

A sanctioned user successfully withdraws collateral (ERC20 tokens) from the protocol despite being on the sanctions list. This directly violates the protocol's OFAC/AML compliance invariant. The asset delta is concrete: the sanctioned address (or a `sendTo` address they control) receives real ERC20 tokens that the sanctions system was intended to block.

---

### Likelihood Explanation

Any user who anticipates imminent sanctioning (e.g., observing a pending sanctions-list update on-chain or off-chain) can frontrun it. The 3-day `SLOW_MODE_TX_DELAY` window is long enough that the sanction will typically be applied before execution, making this a realistic and deliberate exploit path. No privileged access is required — `submitSlowModeTransaction` is a public entry point. [5](#0-4) 

---

### Recommendation

Add a `requireUnsanctioned` check at **execution time** inside `processSlowModeTransactionImpl`, specifically for `WithdrawCollateral` (and any other value-extracting transaction types). The check should validate the `sender` stored in the `SlowModeTx` struct at the point of execution, not only at submission. For example:

```solidity
} else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
    IEndpoint.WithdrawCollateral memory txn = abi.decode(...);
    validateSender(txn.sender, sender);
    requireUnsanctioned(sender); // <-- add this
    clearinghouse.withdrawCollateral(...);
}
```

---

### Proof of Concept

1. User `A` holds collateral in a Nado subaccount and learns they are about to be added to the sanctions list.
2. `A` calls `submitSlowModeTransaction(WithdrawCollateral{...})` — `requireUnsanctioned(A)` passes because `A` is not yet sanctioned.
3. The slow-mode transaction is queued with `executableAt = block.timestamp + 3 days`.
4. `A` is added to the sanctions list (e.g., via Chainalysis oracle update).
5. After 3 days, anyone calls `executeSlowModeTransaction()`.
6. `_executeSlowModeTransaction` → `processSlowModeTransaction` → `processSlowModeTransactionImpl` executes the `WithdrawCollateral` branch with **no sanctions check**.
7. `clearinghouse.withdrawCollateral` transfers ERC20 tokens to `A` (or a `sendTo` address `A` specified), bypassing the sanctions restriction entirely. [6](#0-5) [7](#0-6)

### Citations

**File:** core/contracts/EndpointTx.sol (L202-229)
```text
    function processSlowModeTransactionImpl(
        address sender,
        bytes calldata transaction
    ) public {
        IEndpoint.TransactionType txType = IEndpoint.TransactionType(
            uint8(transaction[0])
        );
        if (txType == IEndpoint.TransactionType.DepositCollateral) {
            IEndpoint.DepositCollateral memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.DepositCollateral)
            );
            validateSender(txn.sender, sender);
            _recordSubaccount(txn.sender);
            clearinghouse.depositCollateral(txn);
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

**File:** core/contracts/EndpointTx.sol (L374-385)
```text
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

**File:** core/contracts/EndpointStorage.sol (L121-123)
```text
    function requireUnsanctioned(address sender) internal view virtual {
        require(!sanctions.isSanctioned(sender), ERR_WALLET_SANCTIONED);
    }
```
