### Title
`purgeStateGuardRole` Does Not Clear `pendingStateGuard`, Allowing Unauthorized Guard Takeover After Purge — (`smart-contracts-poc/contracts/oracles/providers/OracleBase.sol`)

---

### Summary

When the current `stateGuard` for a feed calls `purgeStateGuardRole` to relinquish control, the `pendingStateGuard` mapping is not cleared. If the guard had previously nominated a successor via `setStateGuardRole`, that pending nominee can still call `acceptStateGuardRole` and seize the guard role without any new authorization from ADMIN — an exact structural analog to M-04.

---

### Finding Description

`OracleBase.sol` (providers) implements a two-step guard-transfer pattern:

1. Current guard calls `setStateGuardRole(feedId, newGuard)` → sets `pendingStateGuard[feedId] = newGuard`
2. Nominee calls `acceptStateGuardRole(feedId)` → clears pending, sets `stateGuard[feedId] = msg.sender`

The `purgeStateGuardRole` function is the only path to remove the active guard: [1](#0-0) 

```solidity
function purgeStateGuardRole(bytes32 feedId) external checkRole(feedId) {
    delete stateGuard[feedId];
    emit StateGuardDeleted(feedId);
}
```

It deletes `stateGuard[feedId]` but **never touches `pendingStateGuard[feedId]`**. Compare with `purgePendingStateGuardRole`, which correctly clears the pending slot: [2](#0-1) 

After `purgeStateGuardRole`, `checkRole` falls back to requiring `ADMIN_ROLE`: [3](#0-2) 

But `acceptStateGuardRole` only checks `pendingStateGuard[feedId] == msg.sender` — it does not require ADMIN approval: [4](#0-3) 

So any address that was previously nominated (but never accepted) can call `acceptStateGuardRole` after the guard is purged and become the new `stateGuard` without ADMIN's knowledge or consent.

---

### Impact Explanation

The `stateGuard` role controls `setPriceGuard`, which sets the `[min, max]` price bounds for a feed: [5](#0-4) 

These bounds are stored at the oracle level and enforced at the price provider layer (confirmed by test commentary: *"Guards are stored but NOT enforced at the oracle (they live in the price provider)"*). An unauthorized `stateGuard` can:

- **Widen bounds to `[0, type(uint128).max]`**: allows stale or manipulated prices to pass the price provider's guard, causing bad-price execution in pool swaps.
- **Narrow bounds below the live price**: causes the price provider to reject valid prices, breaking swap and liquidity flows for the affected feed.

Both outcomes fall within the allowed impact gate (bad-price execution; broken core pool functionality causing loss of funds or unusable swap flows).

---

### Likelihood Explanation

The trigger requires:
1. A current `stateGuard` to have previously called `setStateGuardRole` nominating an address (a normal operational step during guard rotation).
2. The same guard to then call `purgeStateGuardRole` without first calling `purgePendingStateGuardRole` to cancel the nomination.

This is a realistic operational sequence — a guard may decide mid-rotation to abandon the transfer and return control to ADMIN, not realizing the pending slot survives. The nominated address (which may be an attacker who was briefly trusted) can then silently accept.

---

### Recommendation

In `purgeStateGuardRole`, also delete the pending nomination:

```solidity
function purgeStateGuardRole(bytes32 feedId) external checkRole(feedId) {
    delete pendingStateGuard[feedId]; // ← add this
    delete stateGuard[feedId];
    emit StateGuardDeleted(feedId);
}
```

This mirrors the correct pattern already used in `acceptStateGuardRole`, which clears `pendingStateGuard` before promoting the new guard.

---

### Proof of Concept

```
1. ADMIN calls setStateGuardRole(feedId, guardA)
2. guardA calls acceptStateGuardRole(feedId)
   → stateGuard[feedId] = guardA, pendingStateGuard[feedId] = address(0)

3. guardA calls setStateGuardRole(feedId, attacker)
   → pendingStateGuard[feedId] = attacker

4. guardA changes mind, calls purgeStateGuardRole(feedId)
   → stateGuard[feedId] = address(0)
   → pendingStateGuard[feedId] = attacker  ← NOT cleared

5. checkRole now requires ADMIN_ROLE — ADMIN believes they are back in control.

6. attacker calls acceptStateGuardRole(feedId)
   → pendingStateGuard[feedId] == attacker ✓ (check passes)
   → stateGuard[feedId] = attacker
   → attacker is now the stateGuard with no ADMIN approval

7. attacker calls setPriceGuard(feedId, 0, type(uint128).max)
   → price guard is widened to accept any price
   → price provider enforces these bounds on pool swaps
   → bad-price execution becomes possible
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L65-74)
```text
    modifier checkRole(bytes32 feedId) {
        address _guard = stateGuard[feedId];
        if (_guard != address(0)) {
            require(_guard == msg.sender, InvalidGuard(msg.sender));
        } else {
            _checkRole(ADMIN_ROLE);
        }

        _;
    }
```

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

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L105-109)
```text
    function purgePendingStateGuardRole(bytes32 feedId) external checkRole(feedId) {
        delete pendingStateGuard[feedId];

        emit PendingStateGuardDeleted(feedId);
    }
```

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L111-118)
```text
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
