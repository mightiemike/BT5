### Title
`WithdrawCollateralV2` Unhandled in `processSlowModeTransactionImpl` Causes Silent Slow-Mode Withdrawal Failure and Fee Loss — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

`submitSlowModeTransactionImpl` accepts `WithdrawCollateralV2` slow-mode submissions from any unprivileged user and charges the slow-mode fee. However, `processSlowModeTransactionImpl` has no branch for `WithdrawCollateralV2` and unconditionally reverts on it. The result is that any user who submits a `WithdrawCollateralV2` slow-mode transaction loses their slow-mode fee and their withdrawal is never executed — permanently breaking the censorship-resistance escape hatch for V2 withdrawals.

---

### Finding Description

`submitSlowModeTransactionImpl` classifies transaction types into two groups: an owner-only set (which requires `sender == owner()`) and everything else (which charges the slow-mode fee and enqueues the transaction). `WithdrawCollateralV2` is **not** in the owner-only set, so it falls into the `else` branch: [1](#0-0) 

This means any user can submit a `WithdrawCollateralV2` slow-mode transaction, paying `SLOW_MODE_FEE`, and have it enqueued.

When the sequencer (or anyone) later calls `executeSlowModeTransaction`, the queued transaction is dispatched to `processSlowModeTransactionImpl`. That function handles `WithdrawCollateral` (V1): [2](#0-1) 

But it has **no branch** for `WithdrawCollateralV2`. The function ends with: [3](#0-2) 

So every `WithdrawCollateralV2` slow-mode transaction unconditionally reverts on execution. The slow-mode fee paid at submission time is not refunded.

By contrast, `processTransactionImpl` (the fast sequencer path) **does** handle `WithdrawCollateralV2` correctly: [4](#0-3) 

The asymmetry between the two handlers is the root cause.

---

### Impact Explanation

1. **Fee loss**: Any user who submits a `WithdrawCollateralV2` slow-mode transaction pays `SLOW_MODE_FEE` (charged in `submitSlowModeTransactionImpl`) but receives nothing — the withdrawal reverts and the fee is not returned.

2. **Censorship-resistance degradation**: The slow-mode queue is the protocol's escape hatch for users whose transactions are being censored by the sequencer. `WithdrawCollateralV2` adds the `sendTo` field (directing funds to an arbitrary address). Users who specifically need this feature (e.g., withdrawing to a cold wallet while being censored) have no functional slow-mode path. They must fall back to V1, which forces funds to `address(uint160(bytes20(sender)))`.

3. **Potential queue stall**: If a `WithdrawCollateralV2` slow-mode transaction is enqueued and the execution path processes transactions sequentially without skipping reverts, the queue can be stalled at that entry, blocking all subsequent slow-mode transactions until the entry is manually cleared.

---

### Likelihood Explanation

The entry path is fully unprivileged: any EOA can call `submitSlowModeTransaction` with a `WithdrawCollateralV2`-typed payload. No special role, governance action, or sequencer compromise is required. The scenario is realistic whenever a user attempts to use the V2 withdrawal path through slow mode (e.g., during sequencer downtime or censorship). The fee loss is immediate and deterministic.

---

### Recommendation

Add a `WithdrawCollateralV2` branch to `processSlowModeTransactionImpl` in `core/contracts/EndpointTx.sol`, mirroring the existing `WithdrawCollateral` branch but decoding `SignedWithdrawCollateralV2` and forwarding `sendTo` to `clearinghouse.withdrawCollateral`. Alternatively, if `WithdrawCollateralV2` is intentionally unsupported via slow mode, add it to the `revert()` guard in `submitSlowModeTransactionImpl` so submissions are rejected at the gate rather than silently accepted and later reverted.

---

### Proof of Concept

1. User Alice constructs a `WithdrawCollateralV2` transaction payload (byte `0` = enum value `44` for `WithdrawCollateralV2`).
2. Alice calls `submitSlowModeTransaction(payload)`.
   - `txType` is `WithdrawCollateralV2`, not in the owner-only list → `else` branch executes.
   - `chargeSlowModeFee` deducts `SLOW_MODE_FEE` from Alice's balance; `slowModeFees += SLOW_MODE_FEE`.
   - Transaction is appended to `slowModeTxs`.
3. After `SLOW_MODE_TX_DELAY`, anyone calls `executeSlowModeTransaction()`.
   - `processSlowModeTransaction` → `processSlowModeTransactionImpl` is called.
   - None of the `if/else if` branches match `WithdrawCollateralV2`.
   - Execution reaches `else { revert(); }`.
4. The call reverts. Alice's slow-mode fee is permanently lost. Her withdrawal is never executed. If the queue is FIFO and non-skippable, all subsequent slow-mode transactions are also blocked. [5](#0-4) [6](#0-5)

### Citations

**File:** core/contracts/EndpointTx.sol (L202-329)
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
            clearinghouse.depositInsurance(transaction);
        } else if (txType == IEndpoint.TransactionType.LinkSigner) {
            IEndpoint.LinkSigner memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.LinkSigner)
            );
            validateSender(txn.sender, sender);
            requireSubaccount(txn.sender);
            linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
        } else if (txType == IEndpoint.TransactionType.WithdrawInsurance) {
            clearinghouse.withdrawInsurance(transaction, nSubmissions);
        } else if (txType == IEndpoint.TransactionType.DelistProduct) {
            clearinghouse.delistProduct(transaction);
        } else if (txType == IEndpoint.TransactionType.DumpFees) {
            IOffchainExchange(offchainExchange).dumpFees();
            uint32[] memory spotIds = spotEngine.getProductIds();
            int128[] memory fees = new int128[](spotIds.length);
            for (uint256 i = 0; i < spotIds.length; i++) {
                fees[i] = sequencerFee[spotIds[i]];
                sequencerFee[spotIds[i]] = 0;
            }
            requireSubaccount(X_ACCOUNT);
            clearinghouse.claimSequencerFees(fees);
        } else if (txType == IEndpoint.TransactionType.RebalanceXWithdraw) {
            clearinghouse.rebalanceXWithdraw(transaction, nSubmissions);
        } else if (txType == IEndpoint.TransactionType.UpdateTierFeeRates) {
            IEndpoint.UpdateTierFeeRates memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.UpdateTierFeeRates)
            );
            IOffchainExchange(offchainExchange).updateTierFeeRates(txn);
        } else if (txType == IEndpoint.TransactionType.AddNlpPool) {
            IEndpoint.AddNlpPool memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.AddNlpPool)
            );
            addNlpPool(txn.owner, txn.balanceWeightX18);
        } else if (txType == IEndpoint.TransactionType.UpdateNlpPool) {
            IEndpoint.UpdateNlpPool memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.UpdateNlpPool)
            );
            updateNlpPool(txn.poolId, txn.owner, txn.balanceWeightX18);
        } else if (txType == IEndpoint.TransactionType.DeleteNlpPool) {
            IEndpoint.DeleteNlpPool memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.DeleteNlpPool)
            );
            deleteNlpPool(txn.poolId);
        } else if (txType == IEndpoint.TransactionType.ForceRebalanceNlpPool) {
            IEndpoint.ForceRebalanceNlpPool memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.ForceRebalanceNlpPool)
            );
            clearinghouse.forceRebalanceNlpPool(
                nlpPools,
                txn.nlpPoolRebalanceX18
            );
        } else if (txType == IEndpoint.TransactionType.NlpProfitShare) {
            IEndpoint.NlpProfitShare memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.NlpProfitShare)
            );
            require(
                txn.poolId > 0 && txn.poolId < nlpPools.length,
                ERR_INVALID_NLP_POOL
            );
            require(
                nlpPools[txn.poolId].owner != address(0),
                ERR_INVALID_NLP_POOL
            );
            require(
                address(uint160(bytes20(txn.recipient))) ==
                    nlpPools[txn.poolId].owner,
                ERR_UNAUTHORIZED
            );
            requireSubaccount(txn.recipient);
            require(!RiskHelper.isIsolatedSubaccount(txn.recipient));
            clearinghouse.nlpProfitShare(
                nlpPools[txn.poolId].subaccount,
                txn.recipient,
                txn.amount
            );
        } else if (txType == IEndpoint.TransactionType.UpdateBuilder) {
            IOffchainExchange(offchainExchange).updateBuilder(transaction);
        } else if (txType == IEndpoint.TransactionType.ClaimBuilderFee) {
            IEndpoint.ClaimBuilderFee memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.ClaimBuilderFee)
            );
            validateSender(txn.sender, sender);
            requireSubaccount(txn.sender);
            IOffchainExchange(offchainExchange).claimBuilderFee(
                txn.sender,
                txn.builderId
            );
        } else {
            revert();
        }
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

**File:** core/contracts/EndpointTx.sol (L437-465)
```text
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateralV2) {
            IEndpoint.SignedWithdrawCollateralV2 memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedWithdrawCollateralV2)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                signedTx.tx.sendTo == address(0)
            );
            int128 currentFeeX18 = spotEngine
                .getConfig(signedTx.tx.productId)
                .withdrawFeeX18;
            require(signedTx.feeX18 >= 0);
            require(signedTx.feeX18 <= currentFeeX18);
            chargeFee(
                signedTx.tx.sender,
                signedTx.feeX18,
                signedTx.tx.productId
            );
            clearinghouse.withdrawCollateral(
                signedTx.tx.sender,
                signedTx.tx.productId,
                signedTx.tx.amount,
                signedTx.tx.sendTo,
                nSubmissions
            );
```
