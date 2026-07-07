### Title
Stale Slow-Mode `LinkSigner` Executes Without Expiry, Enabling Unauthorized Signer Restoration — (File: `core/contracts/EndpointTx.sol`, `core/contracts/Endpoint.sol`)

---

### Summary

`SlowModeConfig` defines a `timeout` field that is never enforced during execution. Slow-mode transactions have a minimum delay (`executableAt`) but no upper-bound expiry. Combined with the absence of nonce validation in the slow-mode `LinkSigner` processing path, a stale queued `LinkSigner` transaction can overwrite a user's current linked signer at any point in the future — including after the user has rotated away from a compromised key.

---

### Finding Description

`IEndpoint.SlowModeConfig` is defined with three fields:

```solidity
struct SlowModeConfig {
    uint64 timeout;
    uint64 txCount;
    uint64 txUpTo;
}
``` [1](#0-0) 

The `timeout` field is never read or enforced anywhere. `_executeSlowModeTransaction` only checks a lower-bound (`executableAt <= block.timestamp`) and never an upper-bound:

```solidity
require(
    fromSequencer || (txn.executableAt <= block.timestamp),
    ERR_SLOW_TX_TOO_RECENT
);
``` [2](#0-1) 

Slow-mode transactions are stored with only a minimum delay:

```solidity
slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
    executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
    sender: sender,
    tx: transaction
});
``` [3](#0-2) 

There is no cancellation mechanism and no expiry timestamp. A queued transaction remains executable indefinitely.

The slow-mode `LinkSigner` processing path in `processSlowModeTransactionImpl` does **not** validate the nonce:

```solidity
} else if (txType == IEndpoint.TransactionType.LinkSigner) {
    IEndpoint.LinkSigner memory txn = abi.decode(transaction[1:], (IEndpoint.LinkSigner));
    validateSender(txn.sender, sender);
    requireSubaccount(txn.sender);
    linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
}
``` [4](#0-3) 

Contrast this with the fast-path `LinkSigner` in `processTransactionImpl`, which calls `validateSignedTx` and enforces the nonce:

```solidity
validateSignedTx(signedTx.tx.sender, signedTx.tx.nonce, transaction, signedTx.signature, true);
linkedSigners[signedTx.tx.sender] = address(uint160(bytes20(signedTx.tx.signer)));
``` [5](#0-4) 

Because the slow-mode path skips nonce validation and the transaction never expires, a queued `LinkSigner` will unconditionally overwrite `linkedSigners[sender]` whenever it is eventually dequeued — regardless of how many fast-path signer changes have occurred in the interim.

---

### Impact Explanation

A linked signer has full signing authority over a subaccount. It can authorize `WithdrawCollateral`, `WithdrawCollateralV2`, `TransferQuote`, `LiquidateSubaccount`, and `MintNlp`/`BurnNlp` on behalf of the subaccount owner. Restoring a stale (potentially compromised) signer gives an attacker the ability to drain the victim's collateral or manipulate their positions.

The corrupted state is `linkedSigners[subaccount]` in `EndpointStorage`, which directly controls who can authorize fund movements. [6](#0-5) 

---

### Likelihood Explanation

The scenario is realistic: a user rotates their linked signer after a key compromise or as routine security hygiene. The slow-mode path is the censorship-resistance mechanism users rely on when the sequencer is unresponsive, making it a natural choice for signer management. Once the slow-mode transaction is in the queue, the user has no way to cancel it. The 3-day delay gives the user a false sense of safety after rotating via the fast path.

---

### Recommendation

1. **Enforce the `timeout` field**: In `_executeSlowModeTransaction`, add an upper-bound check:
   ```solidity
   require(
       fromSequencer ||
       (txn.executableAt <= block.timestamp &&
        block.timestamp <= txn.executableAt + slowModeConfig.timeout),
       ERR_SLOW_TX_EXPIRED
   );
   ```
2. **Add nonce validation to slow-mode `LinkSigner`**: Decode and validate the nonce in `processSlowModeTransactionImpl` for `LinkSigner`, consistent with the fast-path treatment.
3. **Add a cancellation function**: Allow the original sender to cancel a pending slow-mode transaction before it executes.

---

### Proof of Concept

1. Alice submits `submitSlowModeTransaction(LinkSigner{sender: alice, signer: X, nonce: 0})`. The transaction is stored with `executableAt = now + 3 days`. Alice's nonce is not checked or incremented at this point.
2. Alice discovers key X is compromised. She immediately submits a fast-path `LinkSigner` via the sequencer, linking signer Y. `validateSignedTx` increments her nonce to 1. `linkedSigners[alice] = Y`.
3. Three days pass. Anyone calls `executeSlowModeTransaction()`. `_executeSlowModeTransaction` dequeues Alice's stale transaction. `processSlowModeTransactionImpl` reaches the `LinkSigner` branch, calls only `validateSender` (passes, since `msg.sender == alice`), and executes `linkedSigners[alice] = X`.
4. The attacker controlling key X can now call `WithdrawCollateral` or `TransferQuote` signed by X, draining Alice's subaccount. [7](#0-6) [4](#0-3)

### Citations

**File:** core/contracts/interfaces/IEndpoint.sol (L303-307)
```text
    struct SlowModeConfig {
        uint64 timeout;
        uint64 txCount;
        uint64 txUpTo;
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

**File:** core/contracts/EndpointTx.sol (L232-239)
```text
        } else if (txType == IEndpoint.TransactionType.LinkSigner) {
            IEndpoint.LinkSigner memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.LinkSigner)
            );
            validateSender(txn.sender, sender);
            requireSubaccount(txn.sender);
            linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
```

**File:** core/contracts/EndpointTx.sol (L376-380)
```text
        slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: transaction
        });
```

**File:** core/contracts/EndpointTx.sol (L581-589)
```text
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            linkedSigners[signedTx.tx.sender] = address(
                uint160(bytes20(signedTx.tx.signer))
```

**File:** core/contracts/EndpointStorage.sol (L50-50)
```text
    mapping(bytes32 => address) internal linkedSigners;
```
