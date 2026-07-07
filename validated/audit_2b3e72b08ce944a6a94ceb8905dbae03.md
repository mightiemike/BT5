### Title
`WithdrawCollateralV2` Submitted via Slow Mode Silently Fails, Permanently Blocking Permissionless Withdrawal to Custom Address — (`core/contracts/EndpointTx.sol`)

---

### Summary

`WithdrawCollateralV2` is accepted by `submitSlowModeTransactionImpl` (slow mode fee charged), but is never handled in `processSlowModeTransactionImpl`. When the queued transaction is executed permissionlessly via `executeSlowModeTransaction`, it silently reverts and is discarded. The user loses the slow mode fee and the withdrawal never executes. Critically, there is no permissionless path to withdraw to a custom `sendTo` address at all — the only working slow mode withdrawal type (`WithdrawCollateral` V1) hardcodes `sendTo = address(0)`, which resolves to the subaccount owner's own address.

---

### Finding Description

`submitSlowModeTransactionImpl` in `EndpointTx.sol` routes transaction types into three buckets:

1. `DepositCollateral` → immediate revert
2. Admin-only types → `require(sender == owner())`
3. Everything else → charge slow mode fee and enqueue

`WithdrawCollateralV2` (enum value 32) is not in the admin-only list and is not `DepositCollateral`, so it falls into bucket 3: the fee is charged and the transaction is queued. [1](#0-0) 

However, `processSlowModeTransactionImpl` — called when anyone executes the queued transaction via `executeSlowModeTransaction` — has no case for `WithdrawCollateralV2`. It falls into the terminal `else { revert(); }` branch. [2](#0-1) 

`_executeSlowModeTransaction` wraps the call in a `try/catch` that silently swallows the revert (unless OOG), so the failure is invisible to the caller. [3](#0-2) 

The slow mode fee is charged at submission time via `chargeSlowModeFee`, which performs an ERC20 `safeTransferFrom`. There is no refund path on execution failure (the comment "try return funds now removed" confirms this was intentionally removed). [4](#0-3) 

The only working slow mode withdrawal type, `WithdrawCollateral` (V1), hardcodes `sendTo = address(0)` in `processSlowModeTransactionImpl`, which `Clearinghouse.withdrawCollateral` resolves to the subaccount owner's own address. [5](#0-4) [6](#0-5) 

`WithdrawCollateralV2` exists precisely to support a caller-specified `sendTo` address. [7](#0-6) 

---

### Impact Explanation

Two concrete impacts:

**1. Slow mode fee loss with silent withdrawal failure.** Any user who submits `WithdrawCollateralV2` via `submitSlowModeTransaction` pays the slow mode fee immediately. After the 3-day delay, `executeSlowModeTransaction` silently discards the transaction. The user's funds remain locked and the fee is unrecoverable. They must resubmit using V1 and wait another 3 days — a minimum 6-day delay plus double fee cost.

**2. No permissionless path to withdraw to a custom address.** `WithdrawCollateralV2` is the only withdrawal type that supports `sendTo != address(0)`. Since it silently fails in slow mode, there is no permissionless escape hatch for withdrawing to a different address. This is security-critical: a user whose signing key is compromised and who needs to redirect funds to a safe address during sequencer censorship has no on-chain recourse. V1 slow mode will only send to the compromised address.

---

### Likelihood Explanation

No preconditions beyond normal user interaction. Any user who:
- Reads the ABI and sees `WithdrawCollateralV2` as a supported transaction type, or
- Needs to withdraw to a custom address during sequencer downtime/censorship

will trigger this silently. The `WithdrawCollateralV2` type is a first-class enum member and is handled in the sequencer path (`processTransactionImpl`), giving users no reason to suspect it is unsupported in the slow mode path. [8](#0-7) [9](#0-8) 

---

### Recommendation

Add a `WithdrawCollateralV2` case to `processSlowModeTransactionImpl` in `EndpointTx.sol`, mirroring the existing `WithdrawCollateral` case but decoding `WithdrawCollateralV2` and passing `txn.sendTo` to `clearinghouse.withdrawCollateral`. Signature validation is not required in the slow mode path (the sender is already authenticated at submission time via `validateSender`).

---

### Proof of Concept

1. User calls `Endpoint.submitSlowModeTransaction(encodedWithdrawCollateralV2)`.
2. `submitSlowModeTransactionImpl` reaches the `else` branch: `chargeSlowModeFee` transfers the fee from the user, the transaction is enqueued. [10](#0-9) 
3. After `SLOW_MODE_TX_DELAY` (3 days), anyone calls `Endpoint.executeSlowModeTransaction()`.
4. `_executeSlowModeTransaction` calls `this.processSlowModeTransaction(txn.sender, txn.tx)` inside a `try/catch`. [11](#0-10) 
5. `processSlowModeTransactionImpl` receives `txType == WithdrawCollateralV2` (32), finds no matching case, hits `else { revert(); }`. [12](#0-11) 
6. The `catch` block swallows the revert (gas check passes). The transaction is deleted from the queue. The user's withdrawal never executes and the slow mode fee is not refunded.

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

**File:** core/contracts/EndpointTx.sol (L316-329)
```text
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

**File:** core/contracts/EndpointTx.sol (L355-384)
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

**File:** core/contracts/Clearinghouse.sol (L404-406)
```text
        if (sendTo == address(0)) {
            sendTo = address(uint160(bytes20(sender)));
        }
```

**File:** core/contracts/interfaces/IEndpoint.sol (L11-47)
```text
    enum TransactionType {
        LiquidateSubaccount,
        DepositCollateral,
        WithdrawCollateral,
        SpotTick,
        UpdatePrice,
        SettlePnl,
        MatchOrders,
        DepositInsurance,
        ExecuteSlowMode,
        DumpFees,
        PerpTick,
        ManualAssert,
        UpdateProduct, // deprecated
        LinkSigner,
        UpdateFeeTier,
        TransferQuote,
        RebalanceXWithdraw,
        AssertCode,
        WithdrawInsurance,
        CreateIsolatedSubaccount,
        DelistProduct,
        MintNlp,
        BurnNlp,
        MatchOrdersWithAmount,
        UpdateTierFeeRates,
        AddNlpPool,
        UpdateNlpPool,
        DeleteNlpPool,
        AssertProduct,
        CloseIsolatedSubaccount,
        UpdateBuilder,
        ClaimBuilderFee,
        WithdrawCollateralV2,
        ForceRebalanceNlpPool,
        NlpProfitShare
    }
```

**File:** core/contracts/interfaces/IEndpoint.sol (L97-110)
```text
    struct WithdrawCollateralV2 {
        bytes32 sender;
        uint32 productId;
        uint128 amount;
        uint64 nonce;
        address sendTo;
        uint128 appendix; // Reserved for forward-compatible withdrawal features.
    }

    struct SignedWithdrawCollateralV2 {
        WithdrawCollateralV2 tx;
        CompactSignature signature;
        int128 feeX18;
    }
```
