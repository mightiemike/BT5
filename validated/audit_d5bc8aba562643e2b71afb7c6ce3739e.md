### Title
`BurnNlp` Has No Slow-Mode Escape Hatch — Sequencer Censorship Permanently Locks User NLP Collateral - (File: `core/contracts/EndpointTx.sol`)

### Summary

`processSlowModeTransactionImpl` does not handle the `BurnNlp` transaction type, while `submitSlowModeTransactionImpl` silently accepts and charges a fee for it. If the sequencer censors a user's `BurnNlp` fast-mode transaction, the user has no on-chain path to redeem their NLP tokens for quote collateral. Their funds are permanently locked.

### Finding Description

Nado's censorship-resistance model relies on the slow-mode queue: users submit transactions directly to `Endpoint.submitSlowModeTransaction`, wait `SLOW_MODE_TX_DELAY` (3 days), then anyone can call `executeSlowModeTransaction` to force execution.

The submission gate in `submitSlowModeTransactionImpl` accepts every transaction type except `DepositCollateral` — all others fall into the `else` branch that charges a fee and enqueues the transaction: [1](#0-0) 

However, the execution handler `processSlowModeTransactionImpl` only handles a fixed list of transaction types and terminates with a hard revert for everything else: [2](#0-1) 

`BurnNlp` (enum value `22`) is absent from that list. The full list of handled types in `processSlowModeTransactionImpl` covers `DepositCollateral`, `WithdrawCollateral`, `DepositInsurance`, `LinkSigner`, and a set of owner-only admin types — but never `BurnNlp`: [3](#0-2) 

When `_executeSlowModeTransaction` runs the queued `BurnNlp`, the inner `try/catch` silently swallows the revert. The comment `// try return funds now removed` confirms the original refund path was deliberately deleted: [4](#0-3) 

The only on-chain path to convert NLP tokens back to quote collateral is `Clearinghouse.burnNlp`, which is exclusively reachable through `processTransactionImpl` (fast mode, sequencer-only) or `processSlowModeTransactionImpl` (slow mode, broken for `BurnNlp`): [5](#0-4) 

When a user mints NLP tokens, their quote balance is debited and their `NLP_PRODUCT_ID` balance is credited: [6](#0-5) 

There is no alternative redemption path. A direct `WithdrawCollateral` slow-mode transaction will fail the health check if the user's quote balance is zero and their only asset is NLP tokens, because the health contribution of NLP tokens does not substitute for the quote amount being withdrawn.

### Impact Explanation

A user who has minted NLP tokens and is subsequently censored by the sequencer cannot recover their quote collateral. The slow-mode queue — the protocol's stated censorship-resistance mechanism — silently discards `BurnNlp` transactions after consuming the user's `SLOW_MODE_FEE`. The user's NLP balance and the underlying quote collateral locked in the NLP pool are permanently inaccessible without sequencer cooperation. This is a direct collateral-lock impact equivalent to the Linea finding.

### Likelihood Explanation

The sequencer is a single centralized address set at initialization: [7](#0-6) 

Only the sequencer can call `submitTransactionsChecked`, which is the only path that reaches `processTransactionImpl` and therefore `BurnNlp`: [8](#0-7) 

The sequencer can be compelled by a government entity (OFAC, etc.) to censor specific addresses, exactly as described in the Linea report. Any user who has minted NLP tokens and is subsequently sanctioned or censored is permanently locked out of their collateral. The likelihood is moderate: NLP minting is a core protocol feature, and regulatory censorship of centralized sequencers is a documented real-world risk.

### Recommendation

Add `BurnNlp` handling to `processSlowModeTransactionImpl` in `EndpointTx.sol`. The slow-mode variant must allow the user to supply `oraclePriceX18` and `nlpPoolRebalanceX18` directly (as these are normally sequencer-provided in `SignedBurnNlp`), or the protocol must introduce a permissionless oracle-price path for slow-mode NLP redemption. At minimum, ensure that any transaction type accepted by `submitSlowModeTransactionImpl` is also handled (or explicitly rejected with a refund) by `processSlowModeTransactionImpl`, so users are never charged a fee for a transaction that will silently fail.

### Proof of Concept

1. User calls `Endpoint.depositCollateral` → quote balance credited after slow-mode delay.
2. User's `BurnNlp` is submitted to the sequencer (fast mode). Sequencer censors it indefinitely.
3. User calls `Endpoint.submitSlowModeTransaction(burnNlpTx)`. `submitSlowModeTransactionImpl` accepts it, charges `SLOW_MODE_FEE` in quote tokens, and enqueues it at index `txCount`.
4. After `SLOW_MODE_TX_DELAY` (3 days), user or anyone calls `Endpoint.executeSlowModeTransaction()`.
5. `_executeSlowModeTransaction` calls `this.processSlowModeTransaction(sender, burnNlpTx)` inside a `try/catch`.
6. `processSlowModeTransactionImpl` reaches `else { revert(); }` because `BurnNlp` is not handled.
7. The `catch` block fires. `gasleft()` is well above the 250 000 threshold, so `invalid()` is not triggered. The transaction is silently dropped.
8. User's NLP balance is unchanged. Quote collateral remains locked in the NLP pool. `SLOW_MODE_FEE` is lost. No further on-chain path exists to redeem the NLP tokens without sequencer cooperation.

### Citations

**File:** core/contracts/EndpointTx.sol (L202-330)
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
    }
```

**File:** core/contracts/EndpointTx.sol (L369-384)
```text
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
```

**File:** core/contracts/Endpoint.sol (L41-41)
```text
        sequencer = _sequencer;
```

**File:** core/contracts/Endpoint.sol (L205-228)
```text
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
```

**File:** core/contracts/Endpoint.sol (L278-279)
```text
        validateSubmissionIdx(idx);
        require(msg.sender == sequencer);
```

**File:** core/contracts/Clearinghouse.sol (L473-477)
```text
        spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, nlpAmount);
        spotEngine.updateBalance(NLP_PRODUCT_ID, N_ACCOUNT, -nlpAmount);

        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -quoteAmount);
        _applyNlpRebalance(spotEngine, nlpPools, nlpPoolRebalanceX18);
```

**File:** core/contracts/Clearinghouse.sol (L485-530)
```text
    function burnNlp(
        IEndpoint.BurnNlp calldata txn,
        int128 oraclePriceX18,
        IEndpoint.NlpPool[] calldata nlpPools,
        int128[] calldata nlpPoolRebalanceX18
    ) external onlyEndpoint {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);

        ISpotEngine spotEngine = _spotEngine();
        spotEngine.updatePrice(NLP_PRODUCT_ID, oraclePriceX18);

        require(txn.nlpAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        int128 nlpAmount = int128(txn.nlpAmount);
        require(
            spotEngine.getNlpUnlockedBalance(txn.sender).amount >= nlpAmount,
            ERR_UNLOCKED_NLP_INSUFFICIENT
        );
        int128 quoteAmount = nlpAmount.mul(oraclePriceX18);
        int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);
        quoteAmount = MathHelper.max(0, quoteAmount - burnFee);

        _validateNlpRebalance(nlpPools, nlpPoolRebalanceX18, -quoteAmount);
        for (uint128 i = 0; i < nlpPoolRebalanceX18.length; i++) {
            require(nlpPoolRebalanceX18[i] <= 0, ERR_INVALID_NLP_REBALANCE);
        }

        spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, -nlpAmount);
        spotEngine.updateBalance(NLP_PRODUCT_ID, N_ACCOUNT, nlpAmount);

        if (quoteAmount > 0) {
            spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, quoteAmount);
            _applyNlpRebalance(spotEngine, nlpPools, nlpPoolRebalanceX18);
        }

        require(
            spotEngine.getBalance(NLP_PRODUCT_ID, txn.sender).amount >= 0,
            ERR_SUBACCT_HEALTH
        );
        // Burning NLP can decrease health if the burn fee exceeds the health improvement
        // from the withdrawal. This check prevents malicious actors from deliberately
        // creating unhealthy subaccounts through NLP burns.
        require(
            getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,
            ERR_SUBACCT_HEALTH
        );
    }
```
