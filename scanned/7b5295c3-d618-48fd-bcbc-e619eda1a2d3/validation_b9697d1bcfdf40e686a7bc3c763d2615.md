### Title
`purgeStateGuardRole()` Does Not Clear `pendingStateGuard`, Allowing a Pending Guard to Bypass Revocation and Corrupt Oracle Price-Guard Bounds — (`smart-contracts-poc/contracts/oracles/providers/OracleBase.sol`)

---

### Summary

`purgeStateGuardRole()` in `OracleBase` (providers) clears `stateGuard[feedId]` but leaves `pendingStateGuard[feedId]` intact. A pending guard that was nominated before the purge can still call the permissionless `acceptStateGuardRole()` to install itself as the new `stateGuard`, gaining full authority to call `setPriceGuard()` and widen or remove the price-guard bounds that `PriceProvider` / `AnchoredPriceProvider` / `ProtectedPriceProvider` rely on to reject out-of-range oracle prices before they reach pool swap math.

---

### Finding Description

`OracleBase` (providers) implements a two-step guard-role transfer:

1. Current guard calls `setStateGuardRole(feedId, newGuard)` → writes `pendingStateGuard[feedId] = newGuard`.
2. Nominee calls `acceptStateGuardRole(feedId)` → writes `stateGuard[feedId] = msg.sender` and clears `pendingStateGuard`. [1](#0-0) 

There is also a separate function to remove the active guard entirely:

```solidity
function purgeStateGuardRole(bytes32 feedId) external checkRole(feedId) {
    delete stateGuard[feedId];          // ← only clears stateGuard
    emit StateGuardDeleted(feedId);
    // pendingStateGuard[feedId] is NOT cleared
}
``` [2](#0-1) 

After `purgeStateGuardRole`, `stateGuard[feedId] == address(0)`, so `checkRole` falls back to `ADMIN_ROLE`. The operator believes the feed is now under ADMIN control. However, `pendingStateGuard[feedId]` is still set to whatever address was nominated before the purge. Because `acceptStateGuardRole` is **permissionless** (no `checkRole`), the pending nominee can call it at any time:

```solidity
function acceptStateGuardRole(bytes32 feedId) external {
    require(pendingStateGuard[feedId] == msg.sender, InvalidGuard(msg.sender));
    delete pendingStateGuard[feedId];
    stateGuard[feedId] = msg.sender;   // ← installs itself despite the purge
    emit StateGuardUpdated(feedId, msg.sender);
}
``` [3](#0-2) 

Once installed, the attacker calls `setPriceGuard(feedId, 0, 0)`, which the provider interprets as unlimited bounds (`guardMax == 0 → type(uint128).max`):

```solidity
(uint128 guardMin, uint128 guardMax) = offchainOracle.priceGuard(offchainFeedId);
guardMax = guardMax == 0 ? type(uint128).max : guardMax;
if (mid < guardMin || mid > guardMax) {
    return (0, type(uint128).max);
}
``` [4](#0-3) 

The same guard check is present in `PriceProvider`, `PriceProviderL2`, `AnchoredPriceProvider`, and `ProtectedPriceProvider`: [5](#0-4) 

With the guard removed, any oracle price — including extreme or manipulated values — passes the guard check and reaches pool swap math.

---

### Impact Explanation

The `priceGuard` is the last on-chain line of defence against an oracle delivering an out-of-range mid price to the pool. Removing it means that if the oracle (Pyth Lazer / Chainlink Data Streams) ever emits a price outside the intended operating range (e.g., due to a feed bug, a stale anchor, or a manipulation event), the provider will not reject it. The pool will then execute swaps at the bad price, causing:

- **Swap conservation failure**: traders receive more output than the oracle/bin curve permits.
- **LP insolvency**: pool balances fail to cover LP claims after a bad-price swap drains one side.

This matches the allowed impact gate: *"Bad-price execution: stale, inverted, unbounded, or unclamped bid/ask quote reaches a pool swap."*

---

### Likelihood Explanation

The trigger requires two conditions:

1. The current guard calls `setStateGuardRole` to nominate a new address, then later calls `purgeStateGuardRole` believing this cancels the pending transfer. This is a realistic operational mistake: `purgeStateGuardRole` sounds like a full reset, and the separate `purgePendingStateGuardRole` function is easy to overlook.
2. The nominated address (now untrusted, e.g., a compromised multisig) calls `acceptStateGuardRole` before the ADMIN notices.

The window between the purge and the ADMIN re-establishing control is the attack surface. Because `acceptStateGuardRole` is permissionless and costs only one transaction, the attacker can front-run any ADMIN remediation.

---

### Recommendation

`purgeStateGuardRole` must atomically clear both `stateGuard` and `pendingStateGuard`:

```solidity
function purgeStateGuardRole(bytes32 feedId) external checkRole(feedId) {
    delete stateGuard[feedId];
    delete pendingStateGuard[feedId];   // ← add this line
    emit StateGuardDeleted(feedId);
}
```

This mirrors the fix recommended in the external report: cancel the pending state before locking down the role.

---

### Proof of Concept

```
1. Guard A is stateGuard[feedId].
2. Guard A calls setStateGuardRole(feedId, attackerAddr)
   → pendingStateGuard[feedId] = attackerAddr
3. Guard A calls purgeStateGuardRole(feedId)
   → stateGuard[feedId] = address(0)   (ADMIN regains checkRole authority)
   → pendingStateGuard[feedId] = attackerAddr  (NOT cleared)
4. Attacker calls acceptStateGuardRole(feedId)
   → stateGuard[feedId] = attackerAddr  (ADMIN loses authority again)
5. Attacker calls setPriceGuard(feedId, 0, 0)
   → priceGuard[feedId] = {min:0, max:0}
6. Provider interprets max==0 as type(uint128).max → guard is effectively disabled.
7. Oracle emits an extreme price P_bad.
8. PriceProvider._getBidAndAskPrice() passes the guard check (0 ≤ P_bad ≤ max).
9. Pool swap executes at P_bad → LP funds drained.
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L88-97)
```text
    function setPriceGuard(bytes32 feedId, uint128 minPrice, uint128 maxPrice)
        external
        checkRole(feedId)
    {
        require(minPrice < maxPrice);

        priceGuard[feedId] = PriceGuard({min: minPrice, max: maxPrice});

        emit PriceGuardUpdated(feedId, minPrice, maxPrice);
    }
```

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L99-118)
```text
    function setStateGuardRole(bytes32 feedId, address newGuard) external checkRole(feedId) {
        pendingStateGuard[feedId] = newGuard;

        emit StateGuardPending(feedId, newGuard);
    }

    function purgePendingStateGuardRole(bytes32 feedId) external checkRole(feedId) {
        delete pendingStateGuard[feedId];

        emit PendingStateGuardDeleted(feedId);
    }

    function acceptStateGuardRole(bytes32 feedId) external {
        require(pendingStateGuard[feedId] == msg.sender, InvalidGuard(msg.sender));

        delete pendingStateGuard[feedId];
        stateGuard[feedId] = msg.sender;

        emit StateGuardUpdated(feedId, msg.sender);
    }
```

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L120-124)
```text
    function purgeStateGuardRole(bytes32 feedId) external checkRole(feedId) {
        delete stateGuard[feedId];

        emit StateGuardDeleted(feedId);
    }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L289-293)
```text
        // Per-leg price guard.
        (uint128 guardMin, uint128 guardMax) = offchainOracle.priceGuard(feedId);
        guardMax = guardMax == 0 ? type(uint128).max : guardMax;
        if (mid < guardMin || mid > guardMax) return (mid, spreadBps, refTime, false);

```
