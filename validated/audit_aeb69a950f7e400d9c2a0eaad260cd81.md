### Title
OOG Heuristic in `_executeSlowModeTransaction` Is Triggered by Gas-Intensive Slow Mode Transactions, Permanently Blocking the Withdrawal Queue — (`core/contracts/Endpoint.sol`)

---

### Summary

`_executeSlowModeTransaction` in `Endpoint.sol` uses a gas-based heuristic to detect out-of-gas (OOG) errors and calls `invalid()` to prevent silent skipping of failed slow mode transactions. However, the same heuristic fires for any slow mode transaction that reverts after consuming more than half the available gas — a condition that is indistinguishable from a true OOG. Because `invalid()` reverts the entire outer transaction (including the prior `delete` of the queue entry), the blocking transaction remains permanently at the head of the slow mode queue, freezing all subsequent slow mode operations including user withdrawals.

---

### Finding Description

In `_executeSlowModeTransaction`, the slow mode transaction is deleted from the queue **before** the try/catch:

```solidity
delete slowModeTxs[_slowModeConfig.txUpTo++];   // line 194 — deleted optimistically
```

Then the inner call is attempted:

```solidity
uint256 gasRemaining = gasleft();
try this.processSlowModeTransaction(txn.sender, txn.tx) {} catch {
    if (gasleft() <= 250000 || gasleft() <= gasRemaining / 2) {
        assembly { invalid() }   // line 219-223
    }
}
```

The intent is correct: if the inner call ran out of gas, the outer frame should not silently mark the transaction as processed. The `invalid()` opcode reverts everything, keeping the transaction in the queue for a retry with more gas.

**The flaw** is that the heuristic `gasleft() <= gasRemaining / 2` is also satisfied by any slow mode transaction that:
1. **Reverts for any reason** (not just OOG), and
2. **Consumes more than half of `gasRemaining`** before reverting.

These two cases are structurally identical from the outer frame's perspective. When the heuristic fires on a legitimate-but-gas-heavy transaction, `invalid()` is called, reverting the entire outer transaction — including the `delete` at line 194. The slow mode transaction is restored to the head of the queue. Every subsequent attempt to drain the queue hits the same path, and the queue is permanently frozen.

This is the direct analog of the Reserve Protocol finding: just as Chainlink oracle deprecation produces an empty-data revert that is indistinguishable from an OOG revert, a gas-intensive slow mode transaction produces a post-call gas reading that is indistinguishable from an OOG event.

---

### Impact Explanation

The slow mode queue is the settlement path for user-initiated operations including `WithdrawCollateral`. A permanently frozen queue means:

- Users cannot withdraw collateral.
- All queued slow mode transactions behind the blocking entry are also frozen.
- There is no in-protocol mechanism to skip or evict a stuck queue entry; recovery requires a contract upgrade.

The funds are not stolen, but they are effectively locked for an indefinite period — matching the severity profile of the Reserve Protocol finding (blocked redemptions and withdrawals until governance replaces the asset).

---

### Likelihood Explanation

`submitSlowModeTransaction` is callable by any external account with no access control. An attacker can submit a crafted slow mode transaction that:

1. Passes initial queue admission checks.
2. Executes a gas-intensive computation path before hitting a revert condition.
3. Consistently consumes more than `gasRemaining / 2` gas on every execution attempt.

On chains with lower block gas limits (Nado targets Ink, an L2), the absolute gas threshold is lower, making it easier to craft a transaction that crosses the `gasRemaining / 2` boundary. Additionally, the fixed `250000` threshold creates a secondary trigger window: any reverting transaction that leaves fewer than 250,000 gas in the outer frame also fires `invalid()`, regardless of the `gasRemaining / 2` ratio.

---

### Recommendation

The root cause is that the gas heuristic cannot distinguish OOG from a gas-heavy-but-legitimate revert. Mitigations include:

1. **Precise OOG detection**: Before the try/catch, record `gasRemaining`. After the catch, check whether `gasleft() < gasRemaining / 64` (the 1/64 rule for a single call frame). This is a tighter bound than `gasRemaining / 2` and is far less likely to be triggered by a legitimate transaction.
2. **Sequencer skip mechanism**: Allow the sequencer (and only the sequencer) to explicitly evict a stuck slow mode transaction from the head of the queue, with the eviction recorded on-chain for auditability.
3. **Gas floor enforcement at submission**: Reject slow mode transactions at submission time if their estimated gas exceeds a safe fraction of the block gas limit.

---

### Proof of Concept

1. Attacker calls `submitSlowModeTransaction` with a crafted transaction payload that, when processed, executes a gas-intensive loop and then reverts (e.g., via a `require(false)` after burning gas).
2. The transaction is enqueued at position `txUpTo`.
3. Anyone (or the sequencer) calls `executeSlowModeTransaction()`.
4. Inside `_executeSlowModeTransaction`, `delete slowModeTxs[txUpTo]` executes at line 194.
5. `processSlowModeTransaction` is called; the inner call consumes `> gasRemaining / 2` gas and reverts.
6. In the catch block, `gasleft() <= gasRemaining / 2` evaluates to `true`.
7. `invalid()` is executed, reverting the entire outer transaction — including the `delete` from step 4.
8. The attacker's transaction is back at the head of the queue.
9. Every subsequent call to `executeSlowModeTransaction()` repeats steps 3–8.
10. All slow mode transactions behind the attacker's entry — including pending `WithdrawCollateral` requests from legitimate users — are permanently frozen. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** core/contracts/Endpoint.sol (L193-194)
```text
        SlowModeTx memory txn = slowModeTxs[_slowModeConfig.txUpTo];
        delete slowModeTxs[_slowModeConfig.txUpTo++];
```

**File:** core/contracts/Endpoint.sol (L205-227)
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
