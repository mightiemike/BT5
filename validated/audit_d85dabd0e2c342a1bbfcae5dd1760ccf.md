### Title
Stale `pendingStateGuard` Persists After `purgeStateGuardRole`, Enabling Unauthorized Oracle Guard Takeover — (`smart-contracts-poc/contracts/oracles/providers/OracleBase.sol`)

---

### Summary

`purgeStateGuardRole` deletes `stateGuard[feedId]` but does not delete `pendingStateGuard[feedId]`. A previously nominated pending guard can call `acceptStateGuardRole` after the active guard has been purged, silently seizing control of the feed's price-guard setter role without ADMIN's knowledge or consent.

---

### Finding Description

`OracleBase` implements a two-step guard-transfer pattern for each feed: [1](#0-0) 

```
mapping(bytes32 => address) public pendingStateGuard;
mapping(bytes32 => address) public stateGuard;
```

`setStateGuardRole` writes to `pendingStateGuard`; `acceptStateGuardRole` promotes it to `stateGuard` and clears the pending slot: [2](#0-1) 

`purgeStateGuardRole` removes the **active** guard but leaves `pendingStateGuard` untouched: [3](#0-2) 

```solidity
function purgeStateGuardRole(bytes32 feedId) external checkRole(feedId) {
    delete stateGuard[feedId];          // ← active guard cleared
    // pendingStateGuard[feedId] NOT cleared
    emit StateGuardDeleted(feedId);
}
```

The `checkRole` modifier falls back to ADMIN when `stateGuard[feedId] == address(0)`: [4](#0-3) 

After `purgeStateGuardRole` executes, ADMIN believes they have recovered full authority over the feed. However, any address that was previously written into `pendingStateGuard[feedId]` can still call `acceptStateGuardRole` and become the new `stateGuard`, because `acceptStateGuardRole` only checks `pendingStateGuard[feedId] == msg.sender`: [5](#0-4) 

---

### Impact Explanation

The `stateGuard` role is the exclusive authority to call `setPriceGuard`, which sets the `min`/`max` price bounds stored per feed: [6](#0-5) 

The oracle test suite confirms these bounds are **not** enforced at the oracle layer — they are read and enforced by the price provider that sits between the oracle and the pool:

```
// Guards are stored but NOT enforced at the oracle (they live in the price provider):
// min above the actual price still returns raw data.
```

An attacker who seizes the `stateGuard` role can set `priceGuard` to `(0, type(uint128).max)`, effectively disabling the price-range safety net that the price provider enforces before delivering a bid/ask quote to the pool. This opens the door to bad-price execution — a swap settling against an oracle price that the guard was specifically configured to reject — matching the "Bad-price execution: unbounded or unclamped bid/ask quote reaches a pool swap" impact class.

---

### Likelihood Explanation

The trigger requires a legitimate `stateGuard` to:
1. Call `setStateGuardRole(feedId, X)` (nominating a pending successor), and then
2. Call `purgeStateGuardRole(feedId)` to relinquish their own role — without first calling `purgePendingStateGuardRole`.

This is a realistic operational mistake: a guard who wants to hand back control to ADMIN calls `purgeStateGuardRole` but forgets the pending slot still exists. The nominated address `X` (which may be an attacker, or a previously trusted party whose trust has since been revoked) can then call `acceptStateGuardRole` at any future time. ADMIN has no on-chain signal that the pending slot is occupied unless they explicitly query `pendingStateGuard`.

---

### Recommendation

Clear `pendingStateGuard` inside `purgeStateGuardRole`:

```diff
function purgeStateGuardRole(bytes32 feedId) external checkRole(feedId) {
    delete stateGuard[feedId];
+   delete pendingStateGuard[feedId];
    emit StateGuardDeleted(feedId);
}
```

This mirrors the fix recommended for the Hats Protocol analog: the "unlink" operation must atomically clear all derived pending state, not just the primary binding.

---

### Proof of Concept

```
1. ADMIN grants stateGuard role to guardA for feedId.
   guardA calls setStateGuardRole(feedId, attacker).
   → pendingStateGuard[feedId] = attacker

2. guardA decides to relinquish control back to ADMIN.
   guardA calls purgeStateGuardRole(feedId).
   → stateGuard[feedId] = address(0)   (ADMIN regains checkRole authority)
   → pendingStateGuard[feedId] = attacker  ← NOT cleared

3. ADMIN believes they have full control. No pending transfer is visible
   unless ADMIN explicitly reads pendingStateGuard[feedId].

4. attacker calls acceptStateGuardRole(feedId).
   Condition: pendingStateGuard[feedId] == attacker  ✓
   → stateGuard[feedId] = attacker
   → pendingStateGuard[feedId] = address(0)

5. attacker now holds the stateGuard role and calls:
   setPriceGuard(feedId, 0, type(uint128).max)
   → price-range guard for the feed is disabled.
   The price provider no longer rejects out-of-range oracle prices,
   allowing bad-price bid/ask quotes to reach live pool swaps.
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L31-32)
```text
    mapping(bytes32 => address) public pendingStateGuard;
    mapping(bytes32 => address) public stateGuard;
```

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
