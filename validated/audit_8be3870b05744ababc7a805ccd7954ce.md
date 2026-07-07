### Title
Missing Access Control on `getNlpUnlockedBalance` Allows Any Caller to Mutate NLP Locked Balance Queue State — (`File: core/contracts/SpotEngine.sol`)

### Summary
`SpotEngine.getNlpUnlockedBalance` is declared `external` with no access control and is non-view: it calls `tryUnlockNlpBalance(subaccount)`, which mutates `nlpLockedBalanceQueues` storage for any arbitrary subaccount. The authorized path for modifying NLP locked balance state (`handleNlpLockedBalance`) is gated by `_assertInternal()`, which restricts callers to the `canApplyDeltas` whitelist (Endpoint, Clearinghouse, OffchainExchange). `getNlpUnlockedBalance` bypasses this gate entirely, allowing any unprivileged caller to trigger the same underlying queue state mutation for any subaccount at any time.

### Finding Description

The `_assertInternal()` guard in `BaseEngine` enforces that only whitelisted contracts (`canApplyDeltas`) may mutate engine state: [1](#0-0) 

The `canApplyDeltas` whitelist is set at initialization to Endpoint, Clearinghouse, and OffchainExchange only: [2](#0-1) 

The authorized NLP locked balance mutation path, `handleNlpLockedBalance`, correctly calls `_assertInternal()` at its entry: [3](#0-2) 

However, `getNlpUnlockedBalance` is `external`, carries no access control modifier, and directly calls `tryUnlockNlpBalance(subaccount)` — a state-mutating operation on `nlpLockedBalanceQueues`: [4](#0-3) 

Any EOA or contract can call `getNlpUnlockedBalance(targetSubaccount)` and trigger the queue state mutation for any subaccount, completely bypassing the `_assertInternal()` gate that protects every other NLP balance mutation path.

### Impact Explanation

`tryUnlockNlpBalance` mutates `nlpLockedBalanceQueues[subaccount]` — specifically the `unlockedBalanceSum` and the queue's internal state. This `unlockedBalanceSum` is the accounting value the protocol uses to determine how much NLP balance is available for withdrawal or further operations. An attacker can:

1. Force the queue state transition for any subaccount at any time, outside the atomic context of an authorized balance update.
2. Desynchronize `unlockedBalanceSum` from the locked queue entries in a way that the authorized flow (`handleNlpLockedBalance` → `updateBalance`) does not expect, since the authorized flow calls `tryUnlockNlpBalance` itself and assumes it is the first to do so in a given operation.
3. If `tryUnlockNlpBalance` has any non-idempotent behavior (e.g., it resets queue pointers or modifies `balanceCount`), calling it out-of-band before an authorized `updateBalance` call could corrupt the queue state, leading to incorrect `unlockedBalanceSum` values and incorrect NLP redemption accounting.

The corrupted state is: `nlpLockedBalanceQueues[subaccount]` — specifically `unlockedBalanceSum` and queue integrity.

### Likelihood Explanation

The entry path requires no privileges: any externally owned account can call `SpotEngine.getNlpUnlockedBalance(targetSubaccount)` directly. The function is `external`, deployed on-chain, and takes only a `bytes32` subaccount argument. No special role, signature, or sequencer interaction is needed.

### Recommendation

Apply the same `_assertInternal()` guard used by all other NLP balance mutation paths:

```solidity
function getNlpUnlockedBalance(bytes32 subaccount)
    external
    returns (Balance memory)
{
    _assertInternal(); // add this
    tryUnlockNlpBalance(subaccount);
    Balance memory balanceSum = nlpLockedBalanceQueues[subaccount]
        .unlockedBalanceSum;
    return balanceSum;
}
```

Alternatively, if read-only access is needed by external callers, split the function into a `view` getter (returning the current `unlockedBalanceSum` without mutation) and a separate internal/authorized function that performs the unlock.

### Proof of Concept

1. Attacker identifies a target subaccount holding NLP tokens with a pending lock expiry.
2. Attacker calls `SpotEngine.getNlpUnlockedBalance(targetSubaccount)` directly — no signature, no sequencer, no privileged key required.
3. `tryUnlockNlpBalance(targetSubaccount)` executes, mutating `nlpLockedBalanceQueues[targetSubaccount]` outside any authorized operation context.
4. When the Clearinghouse or Endpoint subsequently calls `updateBalance` for the same subaccount (which internally calls `handleNlpLockedBalance` → `tryUnlockNlpBalance`), the queue state has already been partially or fully processed, potentially causing double-processing, incorrect `unlockedBalanceSum`, or skipped queue entries depending on `tryUnlockNlpBalance`'s implementation.
5. The result is a desynchronized NLP locked balance accounting state for the target subaccount, reachable by any unprivileged caller. [4](#0-3) [1](#0-0)

### Citations

**File:** core/contracts/BaseEngine.sol (L199-201)
```text
    function _assertInternal() internal view virtual {
        require(canApplyDeltas[msg.sender], ERR_UNAUTHORIZED);
    }
```

**File:** core/contracts/BaseEngine.sol (L215-217)
```text
        canApplyDeltas[_endpointAddr] = true;
        canApplyDeltas[_clearinghouseAddr] = true;
        canApplyDeltas[_offchainExchangeAddr] = true;
```

**File:** core/contracts/SpotEngine.sol (L129-137)
```text
    function getNlpUnlockedBalance(bytes32 subaccount)
        external
        returns (Balance memory)
    {
        tryUnlockNlpBalance(subaccount);
        Balance memory balanceSum = nlpLockedBalanceQueues[subaccount]
            .unlockedBalanceSum;
        return balanceSum;
    }
```

**File:** core/contracts/SpotEngine.sol (L139-142)
```text
    function handleNlpLockedBalance(bytes32 subaccount, int128 amountDelta)
        internal
    {
        _assertInternal();
```
