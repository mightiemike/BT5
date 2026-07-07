### Title
Blacklisted Collateral Recipients Cannot Recover Funds via Slow Mode Due to Hardcoded `sendTo` in `WithdrawCollateral` Slow Mode Path — (`core/contracts/EndpointTx.sol`)

---

### Summary

In `EndpointTx.processSlowModeTransactionImpl`, the `WithdrawCollateral` slow mode path hardcodes `sendTo = address(0)`, which always resolves to the sender's own address inside `Clearinghouse.withdrawCollateral`. `WithdrawCollateralV2` — the only withdrawal variant that supports a caller-specified recipient — is not handled in `processSlowModeTransactionImpl` and reverts if submitted via slow mode. If a user's address is blacklisted in a collateral token (e.g., USDC), every slow mode `WithdrawCollateral` attempt silently fails and is consumed. Since slow mode is the protocol's only censorship-resistance mechanism, a blacklisted user who is also being censored by the sequencer has no on-chain path to recover their collateral.

---

### Finding Description

**Root cause — hardcoded `sendTo` in slow mode `WithdrawCollateral`:**

In `processSlowModeTransactionImpl`, the `WithdrawCollateral` branch always passes `address(0)` as the `sendTo` argument:

```solidity
clearinghouse.withdrawCollateral(
    txn.sender,
    txn.productId,
    txn.amount,
    address(0),   // ← hardcoded; no way for user to override
    nSubmissions
);
``` [1](#0-0) 

Inside `Clearinghouse.withdrawCollateral`, `address(0)` is resolved to the sender's own address:

```solidity
if (sendTo == address(0)) {
    sendTo = address(uint160(bytes20(sender)));
}
handleWithdrawTransfer(token, sendTo, amount, idx);
``` [2](#0-1) 

`handleWithdrawTransfer` then calls `token.safeTransfer(withdrawPool, amount)` followed by `WithdrawPool.submitWithdrawal(token, sendTo, amount, idx)`, which ultimately calls `token.safeTransfer(sendTo, amount)`. [3](#0-2) [4](#0-3) 

If `sendTo` (the sender's address) is blacklisted in the token, `safeTransfer` reverts. The entire slow mode transaction fails and is caught silently:

```solidity
try this.processSlowModeTransaction(txn.sender, txn.tx) {} catch {
    if (gasleft() <= 250000 || gasleft() <= gasRemaining / 2) {
        assembly { invalid() }
    }
    // try return funds now removed
}
``` [5](#0-4) 

The slow mode transaction is consumed (deleted from the queue) but the withdrawal never executes. The user's SpotEngine balance is preserved (the revert undoes the balance decrement), but the underlying tokens remain locked in the `Clearinghouse`.

**`WithdrawCollateralV2` is not available via slow mode:**

`WithdrawCollateralV2` supports a caller-specified `sendTo` field, which would allow a blacklisted user to redirect funds to a clean address. However, `processSlowModeTransactionImpl` does not handle `WithdrawCollateralV2` — it falls through to the terminal `revert()`: [6](#0-5) 

If a user submits a `WithdrawCollateralV2` via `submitSlowModeTransaction`, it is queued and the slow mode fee is charged, but when executed it hits `revert()`, consuming the transaction and the fee without processing the withdrawal. [7](#0-6) 

---

### Impact Explanation

A user whose address is blacklisted in a collateral token (e.g., USDC, which maintains an on-chain blacklist) and who is simultaneously being censored by the sequencer has **no on-chain path to recover their collateral**:

- Slow mode `WithdrawCollateral` always resolves `sendTo` to the blacklisted address → silently fails every time.
- Slow mode `WithdrawCollateralV2` (custom `sendTo`) is not supported → reverts and wastes the slow mode fee.
- Sequencer-submitted `WithdrawCollateralV2` with a custom `sendTo` is the only working path, but it requires sequencer cooperation — which is precisely the scenario slow mode is designed to bypass.

The user's collateral is permanently locked in the `Clearinghouse`. The censorship-resistance guarantee of the slow mode queue is broken for this class of users.

---

### Likelihood Explanation

- USDC is a standard collateral token in DeFi protocols and maintains an on-chain blacklist operated by Circle.
- Sequencer censorship (refusing to include specific users' transactions) is a realistic operational or regulatory scenario — the slow mode queue exists precisely to handle it.
- The combination of both conditions (blacklisted address + sequencer censorship) is realistic and has occurred in other protocols.

---

### Recommendation

Add `WithdrawCollateralV2` handling to `processSlowModeTransactionImpl` so that users can specify an alternative `sendTo` address via slow mode:

```solidity
} else if (txType == IEndpoint.TransactionType.WithdrawCollateralV2) {
    IEndpoint.WithdrawCollateralV2 memory txn = abi.decode(
        transaction[1:],
        (IEndpoint.WithdrawCollateralV2)
    );
    validateSender(txn.sender, sender);
    clearinghouse.withdrawCollateral(
        txn.sender,
        txn.productId,
        txn.amount,
        txn.sendTo,   // ← user-specified recipient
        nSubmissions
    );
}
```

This mirrors the fix recommended in the external report: allow the user to pass an alternative recipient address so that a blacklisted address does not permanently block fund recovery. [1](#0-0) 

---

### Proof of Concept

1. User deposits USDC collateral via `Endpoint.depositCollateral`. Tokens are transferred to `Clearinghouse`.
2. User's address is blacklisted by Circle in the USDC contract.
3. Sequencer begins censoring the user (refuses to include their `WithdrawCollateralV2` transactions).
4. User submits a slow mode `WithdrawCollateral` transaction via `submitSlowModeTransaction`.
5. After the 3-day delay, anyone calls `executeSlowModeTransaction`.
6. `processSlowModeTransactionImpl` calls `clearinghouse.withdrawCollateral(..., address(0), ...)`.
7. `Clearinghouse` resolves `sendTo = user's blacklisted address`.
8. `BaseWithdrawPool.handleWithdrawTransfer` calls `token.safeTransfer(blacklistedAddress, amount)` → reverts with "blacklisted".
9. The catch block in `_executeSlowModeTransaction` silently swallows the revert.
10. The slow mode transaction is consumed. User's SpotEngine balance is intact but tokens remain in `Clearinghouse`.
11. User attempts to submit slow mode `WithdrawCollateralV2` → queued, fee charged, but on execution hits `revert()` in `processSlowModeTransactionImpl`.
12. User has no remaining on-chain path to recover funds. Collateral is permanently locked.

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

**File:** core/contracts/Clearinghouse.sol (L404-408)
```text
        if (sendTo == address(0)) {
            sendTo = address(uint160(bytes20(sender)));
        }

        handleWithdrawTransfer(token, sendTo, amount, idx);
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
