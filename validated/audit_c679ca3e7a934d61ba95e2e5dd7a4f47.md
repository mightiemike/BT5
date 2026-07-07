### Title
Builder Without Registered Subaccount Cannot Claim Accumulated Builder Fees — (`File: core/contracts/EndpointTx.sol`)

---

### Summary

The `ClaimBuilderFee` slow-mode transaction handler in `EndpointTx::processSlowModeTransactionImpl` calls `requireSubaccount(txn.sender)` before forwarding to `IOffchainExchange::claimBuilderFee`. A builder whose fee-recipient subaccount has never been registered on-chain (i.e., has never deposited collateral or been recorded via `_recordSubaccount`) will have their claim silently dropped by the sequencer's try/catch loop, permanently locking their accumulated builder fees and burning their slow-mode fee payment.

---

### Finding Description

In `processSlowModeTransactionImpl`, the `ClaimBuilderFee` branch is:

```solidity
} else if (txType == IEndpoint.TransactionType.ClaimBuilderFee) {
    IEndpoint.ClaimBuilderFee memory txn = abi.decode(
        transaction[1:],
        (IEndpoint.ClaimBuilderFee)
    );
    validateSender(txn.sender, sender);
    requireSubaccount(txn.sender);          // ← overly restrictive gate
    IOffchainExchange(offchainExchange).claimBuilderFee(
        txn.sender,
        txn.builderId
    );
``` [1](#0-0) 

`requireSubaccount` checks that `subaccountIds[txn.sender] != 0`, i.e., the subaccount has been previously registered through a deposit or an explicit `_recordSubaccount` call. [2](#0-1) 

A builder accumulates fees off-chain through the `OffchainExchange` simply by routing trades. The builder's fee-recipient address is set via `UpdateBuilder` and does not require any on-chain deposit. If the builder designates a fresh address (e.g., a cold wallet) as their fee-recipient subaccount, that subaccount will have `subaccountIds == 0`, causing `requireSubaccount` to revert.

The slow-mode execution path wraps `processSlowModeTransaction` in a try/catch:

```solidity
try this.processSlowModeTransaction(txn.sender, txn.tx) {} catch {
    // silent drop unless out-of-gas
}
``` [3](#0-2) 

The revert is silently swallowed. The builder's slow-mode fee (paid in ERC20 at submission time via `chargeSlowModeFee`) is consumed, and the builder fees remain unclaimed. [4](#0-3) 

---

### Impact Explanation

A builder who has legitimately accumulated protocol fees but whose designated subaccount has never deposited collateral is permanently unable to claim those fees. The `requireSubaccount` gate is not needed for a claim operation — the builder's entitlement is established by the off-chain fee accounting in `OffchainExchange`, not by on-chain subaccount registration. The builder also loses the slow-mode fee paid at submission, since the transaction is silently dropped.

---

### Likelihood Explanation

Medium. Builders are protocol-level integrators (front-ends, aggregators) who may reasonably designate a treasury or cold-wallet address as their fee recipient — an address that has never interacted with the Nado clearinghouse. The `UpdateBuilder` path imposes no requirement that the recipient subaccount be registered. Any builder who follows this pattern will be unable to claim fees.

---

### Recommendation

Remove the `requireSubaccount(txn.sender)` check from the `ClaimBuilderFee` branch. The `validateSender` check already ensures the caller controls the subaccount address encoded in `txn.sender`. The entitlement to builder fees is governed entirely by `IOffchainExchange::claimBuilderFee`, which should be the sole authority on whether a claim is valid.

```solidity
} else if (txType == IEndpoint.TransactionType.ClaimBuilderFee) {
    IEndpoint.ClaimBuilderFee memory txn = abi.decode(
        transaction[1:],
        (IEndpoint.ClaimBuilderFee)
    );
    validateSender(txn.sender, sender);
-   requireSubaccount(txn.sender);
    IOffchainExchange(offchainExchange).claimBuilderFee(
        txn.sender,
        txn.builderId
    );
```

---

### Proof of Concept

1. Protocol owner calls `UpdateBuilder` (via `OffchainExchange`) to register a builder with fee-recipient subaccount `0xBUILDER000000000000000000000000000000000064756d6d79` (address `0xBUILDER…`, name `dummy`). This address has never deposited; `subaccountIds[recipient] == 0`.
2. Trades are routed through the builder's interface; fees accumulate in `OffchainExchange` against `builderId`.
3. Builder calls `Endpoint::submitSlowModeTransaction` with a `ClaimBuilderFee` payload. The ERC20 slow-mode fee is deducted from `msg.sender`.
4. Sequencer calls `executeSlowModeTransaction` → `processSlowModeTransaction` → `processSlowModeTransactionImpl`.
5. `requireSubaccount(txn.sender)` reverts because `subaccountIds[txn.sender] == 0`.
6. The outer try/catch in `_executeSlowModeTransaction` silently swallows the revert.
7. Builder fees remain locked in `OffchainExchange`; slow-mode fee is permanently lost. [1](#0-0) [5](#0-4)

### Citations

**File:** core/contracts/EndpointTx.sol (L316-327)
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
```

**File:** core/contracts/EndpointTx.sol (L369-372)
```text
        } else {
            chargeSlowModeFee(_getQuote(), sender);
            slowModeFees += SLOW_MODE_FEE;
        }
```

**File:** core/contracts/Endpoint.sol (L94-101)
```text
    ) internal returns (bool) {
        int256 minDepositAmount = MIN_DEPOSIT_AMOUNT;
        if (subaccount != X_ACCOUNT && (subaccountIds[subaccount] == 0)) {
            minDepositAmount = MIN_FIRST_DEPOSIT_AMOUNT;
        }
        return
            clearinghouse.checkMinDeposit(productId, amount, minDepositAmount);
    }
```

**File:** core/contracts/Endpoint.sol (L185-228)
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
```
