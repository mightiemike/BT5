### Title
No Execution Fee Paid to Caller of `executeSlowModeTransaction` — (`Endpoint.sol`)

---

### Summary

`Endpoint.executeSlowModeTransaction()` is the permissionless censorship-resistance fallback that any external caller can invoke to process queued slow mode transactions (including `WithdrawCollateral`). However, the caller receives **no compensation** for gas costs. The `SLOW_MODE_FEE` collected from users at submission time accumulates in `slowModeFees` and is only claimable by the owner/sequencer via the owner-gated `DumpFees` path — it is never distributed to the executor.

---

### Finding Description

When a user submits a slow mode transaction (e.g., `WithdrawCollateral`) via `submitSlowModeTransaction()`, the protocol charges them `SLOW_MODE_FEE = $1` in quote tokens: [1](#0-0) [2](#0-1) 

This fee is transferred into the `Endpoint` contract via `chargeSlowModeFee()` and accumulated in the `slowModeFees` storage variable: [3](#0-2) [4](#0-3) 

The `slowModeFees` balance is only recoverable through the `DumpFees` slow mode transaction type, which is restricted to `owner()`: [5](#0-4) 

The external function `executeSlowModeTransaction()` — the permissionless fallback for when the sequencer is offline — has no access control and pays **nothing** to the caller: [6](#0-5) 

The caller pays gas to execute the transaction but receives zero compensation. The `SLOW_MODE_FEE` that was explicitly collected from the user to cover execution costs never reaches the executor.

Slow mode transactions are queued with a `SLOW_MODE_TX_DELAY` of 3 days before they become executable by the public: [7](#0-6) [8](#0-7) 

The sequencer can also process slow mode transactions via the `ExecuteSlowMode` transaction type (the normal path). The public `executeSlowModeTransaction()` is the **only** fallback when the sequencer is unavailable.

---

### Impact Explanation

If the sequencer goes offline or censors a user, the user's `WithdrawCollateral` slow mode transaction sits in the queue. After 3 days it becomes publicly executable, but no external party has any economic incentive to call `executeSlowModeTransaction()` — they would pay gas with zero reward. The `SLOW_MODE_FEE` the user already paid is locked in the contract and flows only to the owner/sequencer via `DumpFees`, not to the executor.

The result is that users' collateral withdrawal requests can be permanently blocked whenever the sequencer is unavailable, defeating the purpose of the slow mode censorship-resistance mechanism. The affected transaction types include `WithdrawCollateral`, `LinkSigner`, `ClaimBuilderFee`, and `DepositCollateral` (via `depositCollateralWithReferral`): [9](#0-8) 

---

### Likelihood Explanation

The slow mode path is explicitly designed as a censorship-resistance fallback. Sequencer downtime or targeted censorship of specific users is a realistic operational scenario. The 3-day delay means affected users face a prolonged window during which their collateral is inaccessible. The missing incentive is a structural gap, not a theoretical edge case — it applies to every slow mode transaction submitted by every user.

---

### Recommendation

Distribute a portion of the `SLOW_MODE_FEE` collected at submission time to the `msg.sender` of `executeSlowModeTransaction()`. The fee is already collected in quote tokens and held in the contract. The executor should receive at minimum enough to cover expected gas costs, with the remainder going to `slowModeFees` as today.

```solidity
function executeSlowModeTransaction() external {
    SlowModeConfig memory _slowModeConfig = slowModeConfig;
    _executeSlowModeTransaction(_slowModeConfig, false);
    nSubmissions += 1;
    slowModeConfig = _slowModeConfig;
    // Pay executor from slowModeFees
    uint128 reward = SLOW_MODE_EXECUTOR_REWARD; // e.g. a portion of SLOW_MODE_FEE
    slowModeFees -= int128(reward);
    _getQuote().safeTransfer(msg.sender, reward);
}
```

---

### Proof of Concept

1. User calls `submitSlowModeTransaction()` with a `WithdrawCollateral` transaction, paying `SLOW_MODE_FEE = $1` in quote tokens. The fee is transferred to `Endpoint` and `slowModeFees` is incremented.
2. The sequencer goes offline. The sequencer's normal `ExecuteSlowMode` path is unavailable.
3. After `SLOW_MODE_TX_DELAY = 3 days`, the transaction satisfies `txn.executableAt <= block.timestamp`.
4. Any external caller can now call `executeSlowModeTransaction()`. However, doing so costs gas (potentially significant on mainnet) with zero reward — the `SLOW_MODE_FEE` the user paid is locked in `slowModeFees` and only the owner can claim it via `DumpFees`.
5. No rational external party calls `executeSlowModeTransaction()`. The user's `WithdrawCollateral` remains stuck in the queue indefinitely, blocking their collateral. [10](#0-9) [11](#0-10)

### Citations

**File:** core/contracts/EndpointTx.sol (L202-230)
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
        } else if (txType == IEndpoint.TransactionType.DepositInsurance) {
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

**File:** core/contracts/common/Constants.sol (L23-23)
```text
int128 constant SLOW_MODE_FEE = 1000000; // $1
```

**File:** core/contracts/common/Constants.sol (L50-50)
```text
uint64 constant SLOW_MODE_TX_DELAY = 3 * 24 * 60 * 60; // 3 days
```

**File:** core/contracts/EndpointStorage.sol (L55-55)
```text
    int128 internal slowModeFees;
```

**File:** core/contracts/EndpointStorage.sol (L83-93)
```text
    function chargeSlowModeFee(IERC20Base token, address from)
        internal
        virtual
    {
        require(address(token) != address(0));
        token.safeTransferFrom(
            from,
            address(this),
            clearinghouse.getSlowModeFee()
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
