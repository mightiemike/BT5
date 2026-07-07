### Title
Slow-Mode `LinkSigner` Transaction Has No Expiration or Cancellation, Enabling Stale Signer Reinstatement After Revocation — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

The Nado slow-mode queue (`slowModeTxs`) has no expiration deadline and no cancellation mechanism. A user who submits a slow-mode `LinkSigner` transaction and later revokes that signer via the fast (sequencer) path cannot prevent the stale queued transaction from executing after its 3-day lock, silently reinstating the revoked signer with full signing authority over the subaccount.

---

### Finding Description

`IEndpoint.SlowModeTx` carries only an `executableAt` activation timestamp — there is no expiration field: [1](#0-0) 

The queue is strictly FIFO; `_executeSlowModeTransaction` always pops `slowModeTxs[txUpTo]` and there is no function to cancel or skip an individual entry: [2](#0-1) 

`executeSlowModeTransaction()` is public and callable by any address: [3](#0-2) 

When a slow-mode `LinkSigner` transaction is eventually dequeued, `processSlowModeTransactionImpl` unconditionally overwrites `linkedSigners[txn.sender]` with no timestamp guard, no nonce check, and no staleness check: [4](#0-3) 

By contrast, the fast-path `LinkSigner` (processed by the sequencer) does consume a nonce: [5](#0-4) 

Because the slow-mode path skips nonce validation entirely, a fast-path revocation that increments the nonce does **not** invalidate the already-queued slow-mode entry. The two paths write to the same `linkedSigners` mapping: [6](#0-5) 

---

### Impact Explanation

A linked signer has full authority to sign `WithdrawCollateral`, `TransferQuote`, `MintNlp`, `BurnNlp`, and order-matching transactions on behalf of the subaccount owner. Reinstating a revoked signer therefore grants that address complete control over the victim's funds and positions. The overwrite is silent — no event distinguishes a stale reinstatement from a legitimate update — so the victim may not notice until assets are drained.

---

### Likelihood Explanation

The scenario is realistic and low-friction:

- A user submits a slow-mode `LinkSigner` to delegate to a hot wallet (address B).
- The hot wallet is later compromised, so the user immediately submits a fast-path `LinkSigner` to revoke B (sets signer to `address(0)` or a new address C).
- The user believes B is revoked. Three days later, the queued slow-mode tx executes — reinstating B.
- Any third party can accelerate this by calling `executeSlowModeTransaction()` once the 3-day window passes.

The $1 slow-mode fee paid at submission time is the only cost to the original submitter; no additional attacker cost is required to trigger execution.

---

### Recommendation

Add an `expireAt` field to `SlowModeTx` (e.g., `executableAt + MAX_SLOW_MODE_LIFETIME`). In `_executeSlowModeTransaction`, skip (or revert on) any entry whose `expireAt` has passed. Alternatively, allow the original `sender` to cancel their own queued entry before it executes, or validate that the `linkedSigners` state at execution time still matches the intent encoded in the queued transaction (e.g., by storing the expected prior-signer value and rejecting if it has changed).

---

### Proof of Concept

```
T=0:   User calls submitSlowModeTransaction(LinkSigner{sender=Alice, signer=B})
         → slowModeTxs[N] = {executableAt: T+3days, sender: Alice, tx: LinkSigner{signer=B}}
         → linkedSigners[Alice] = B  (not yet; queued only)

T=1h:  Sequencer processes fast-path LinkSigner{sender=Alice, signer=0x0, nonce=k}
         → nonces[Alice] = k+1
         → linkedSigners[Alice] = address(0)   ← B is revoked

T=3d+: Anyone calls executeSlowModeTransaction()
         → processSlowModeTransactionImpl called with LinkSigner{sender=Alice, signer=B}
         → NO nonce check, NO expiry check
         → linkedSigners[Alice] = B             ← B reinstated

T=3d+: Signer B calls WithdrawCollateral / TransferQuote on Alice's subaccount
         → funds drained
```

The root cause is the absence of any expiration or cancellation path in the slow-mode queue, making every queued `LinkSigner` entry a permanent, irrevocable future state mutation.

### Citations

**File:** core/contracts/interfaces/IEndpoint.sol (L297-301)
```text
    struct SlowModeTx {
        uint64 executableAt;
        address sender;
        bytes tx;
    }
```

**File:** core/contracts/Endpoint.sol (L185-199)
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

**File:** core/contracts/EndpointTx.sol (L576-590)
```text
        } else if (txType == IEndpoint.TransactionType.LinkSigner) {
            IEndpoint.SignedLinkSigner memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedLinkSigner)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            linkedSigners[signedTx.tx.sender] = address(
                uint160(bytes20(signedTx.tx.signer))
            );
```

**File:** core/contracts/EndpointStorage.sol (L50-50)
```text
    mapping(bytes32 => address) internal linkedSigners;
```
