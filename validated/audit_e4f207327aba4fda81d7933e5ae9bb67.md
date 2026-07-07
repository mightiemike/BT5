### Title
Slow Mode Transactions Have No Expiry Deadline, Enabling Stale `LinkSigner` Re-Authorization at Attacker-Chosen Time — (`File: core/contracts/Endpoint.sol`, `core/contracts/EndpointTx.sol`)

---

### Summary

Slow mode transactions in the Nado `Endpoint` enforce a **minimum** delay (`SLOW_MODE_TX_DELAY`, hardcoded to three days) before execution, but impose **no maximum deadline (expiry)**. Once the delay passes, any slow mode transaction can be executed at an arbitrarily distant future time. This is a direct analog to the `resolveRegistration` timing issue: just as a winning vote could be finalized at any moment to surprise users, a stale slow mode `LinkSigner` transaction can be executed long after the user believed the signer was revoked, re-authorizing a compromised or untrusted signer to act on the subaccount.

---

### Finding Description

In `Endpoint.sol`, `depositCollateralWithReferral` and `EndpointTx.submitSlowModeTransactionImpl` both enqueue slow mode transactions with only a lower-bound timestamp:

```solidity
slowModeTxs[_slowModeConfig.txCount++] = SlowModeTx({
    executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
    ...
});
``` [1](#0-0) [2](#0-1) 

The execution guard in `_executeSlowModeTransaction` only checks that the transaction is **not too recent**:

```solidity
require(
    fromSequencer || (txn.executableAt <= block.timestamp),
    ERR_SLOW_TX_TOO_RECENT
);
``` [3](#0-2) 

There is no `executableBefore` or expiry field, and no check that `block.timestamp` is within any upper bound. A slow mode transaction queued today can be executed one year from now.

The public `executeSlowModeTransaction()` function is callable by **any unprivileged user** after the delay:

```solidity
function executeSlowModeTransaction() external {
    SlowModeConfig memory _slowModeConfig = slowModeConfig;
    _executeSlowModeTransaction(_slowModeConfig, false);
    ...
}
``` [4](#0-3) 

Among the slow mode transaction types, `LinkSigner` is the most dangerous. When processed via the slow mode path in `processSlowModeTransactionImpl`, it unconditionally overwrites the linked signer mapping:

```solidity
linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
``` [5](#0-4) 

Meanwhile, the fast-path `LinkSigner` (processed by the sequencer via `processTransactionImpl`) also unconditionally sets the same mapping:

```solidity
linkedSigners[signedTx.tx.sender] = address(
    uint160(bytes20(signedTx.tx.signer))
);
``` [6](#0-5) 

Because the fast path is processed immediately by the sequencer while the slow mode path sits in a FIFO queue, a fast-path revocation (setting signer to `address(0)`) can be silently overridden when the older slow mode `LinkSigner` is eventually executed — at any time chosen by the attacker.

---

### Impact Explanation

The linked signer is used in `validateSignature` / `validateCompactSignature` to authorize all signed transactions on behalf of a subaccount, including `WithdrawCollateral`, `LiquidateSubaccount`, `TransferQuote`, `MintNlp`, `BurnNlp`, and order matching. Re-authorizing a revoked signer gives that signer full transaction authority over the victim's subaccount, enabling collateral theft via withdrawal or position manipulation. [7](#0-6) 

---

### Likelihood Explanation

The attack requires only that:
1. A slow mode `LinkSigner` transaction was submitted at any point in the past (e.g., during initial setup or a prior key rotation).
2. The user later revoked the signer via the fast path.
3. The slow mode tx is still pending in the queue (not yet executed by the sequencer).

The attacker (the revoked signer, or anyone who front-runs the queue) calls `executeSlowModeTransaction()` at a chosen moment — e.g., when the victim's account holds maximum collateral — to re-authorize themselves. This is a low-complexity, unprivileged, externally reachable call.

---

### Recommendation

Add an `executableBefore` (expiry) field to `SlowModeTx` and enforce it in `_executeSlowModeTransaction`:

```solidity
require(
    block.timestamp < txn.executableBefore,
    ERR_SLOW_TX_EXPIRED
);
```

Set `executableBefore = executableAt + MAX_SLOW_MODE_WINDOW` (e.g., 7 days after the minimum delay). Expired transactions should be discarded (funds returned for `WithdrawCollateral`; no state change for `LinkSigner`). This mirrors the recommendation in the external report to add a timeout that discards unresolved candidates after a bounded window.

---

### Proof of Concept

1. Alice submits a slow mode `LinkSigner` transaction naming Eve as her linked signer. The tx is enqueued with `executableAt = T + 3 days`.
2. Alice realizes Eve is malicious and immediately submits a fast-path `LinkSigner` (via the sequencer) setting her signer to `address(0)`. The sequencer processes this, setting `linkedSigners[Alice] = address(0)`.
3. The slow mode `LinkSigner` (Eve) remains in the queue. No expiry exists.
4. Months later, when Alice's account holds significant collateral, Eve calls `executeSlowModeTransaction()`. The FIFO queue reaches Alice's old tx and executes it, setting `linkedSigners[Alice] = Eve`.
5. Eve immediately signs and submits a `WithdrawCollateral` transaction on Alice's behalf, draining her collateral.

The root cause is the absence of an upper-bound deadline in `SlowModeTx.executableAt` and the lack of any expiry check in `_executeSlowModeTransaction`. [8](#0-7) [9](#0-8)

### Citations

**File:** core/contracts/Endpoint.sol (L152-153)
```text
        slowModeTxs[_slowModeConfig.txCount++] = SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
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

**File:** core/contracts/Endpoint.sol (L231-236)
```text
    function executeSlowModeTransaction() external {
        SlowModeConfig memory _slowModeConfig = slowModeConfig;
        _executeSlowModeTransaction(_slowModeConfig, false);
        nSubmissions += 1;
        slowModeConfig = _slowModeConfig;
    }
```

**File:** core/contracts/EndpointTx.sol (L172-184)
```text
    function validateSignature(
        bytes32 sender,
        bytes32 digest,
        bytes memory signature,
        bool allowLinkedSigner
    ) internal virtual {
        verifier.validateSignature(
            sender,
            allowLinkedSigner ? getLinkedSigner(sender) : address(0),
            digest,
            signature
        );
    }
```

**File:** core/contracts/EndpointTx.sol (L239-239)
```text
            linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
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

**File:** core/contracts/EndpointTx.sol (L588-590)
```text
            linkedSigners[signedTx.tx.sender] = address(
                uint160(bytes20(signedTx.tx.signer))
            );
```
