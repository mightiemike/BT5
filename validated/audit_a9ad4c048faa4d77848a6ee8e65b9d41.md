### Title
Malicious `stateGuard` Can Permanently Block Admin Recovery of Oracle Price-Guard Control — (`smart-contracts-poc/contracts/oracles/providers/OracleBase.sol`)

---

### Summary

Once a `stateGuard` is installed for a feed in `providers/OracleBase.sol`, the `ADMIN_ROLE` is completely locked out of all guard-management functions for that feed. A compromised or malicious `stateGuard` can hold the role indefinitely, set extreme `priceGuard` bounds that reject all valid price updates (making the oracle stale), and block any admin-initiated replacement — with no on-chain recovery path.

---

### Finding Description

`providers/OracleBase.sol` implements a two-step guard-transfer pattern via `setStateGuardRole` + `acceptStateGuardRole`. The `checkRole` modifier that gates all guard-management functions branches on whether a `stateGuard` is already set: [1](#0-0) 

When `stateGuard[feedId] == address(0)`, the `ADMIN_ROLE` is the authority. Once a stateGuard is accepted, the modifier exclusively requires `msg.sender == stateGuard[feedId]` — the `ADMIN_ROLE` branch is dead for that feed.

Every guard-management function is gated by `checkRole`:

- `setStateGuardRole` (propose replacement) [2](#0-1) 
- `purgePendingStateGuardRole` (cancel pending) [3](#0-2) 
- `purgeStateGuardRole` (self-remove) [4](#0-3) 
- `setPriceGuard` (set min/max price bounds) [5](#0-4) 

There is **no `ADMIN_ROLE`-only override** to forcibly replace or purge a stateGuard. The `ADMIN_ROLE` has zero on-chain recourse once a stateGuard is installed.

The `acceptStateGuardRole` function is permissionless — it only checks that `msg.sender == pendingStateGuard[feedId]`: [6](#0-5) 

A malicious stateGuard can front-run any admin-initiated replacement by calling `setStateGuardRole(feedId, attackerControlledAddress)` immediately after the admin's proposal, overwriting `pendingStateGuard` before the honest nominee calls `acceptStateGuardRole`. This is the exact race described in the external report.

---

### Impact Explanation

The `stateGuard` controls `setPriceGuard`, which sets the `PriceGuard({min, max})` bounds used to validate incoming price updates for a feed: [5](#0-4) 

A malicious stateGuard can call `setPriceGuard(feedId, 1, 2)` — an impossibly tight band — causing every subsequent price push to be rejected by the guard check. The oracle's stored price becomes permanently stale. Pools consuming this feed via `price(feedId, pool)` then execute swaps against a stale mid/spread, constituting bad-price execution. Traders receive incorrect amounts; the pool's token conservation invariant is violated.

Alternatively, the stateGuard can set `priceGuard` to `{min: 1, max: type(uint128).max - 1}`, effectively disabling the guard and allowing any manipulated price through.

---

### Likelihood Explanation

- The `ADMIN_ROLE` is expected to delegate feed control to per-feed stateGuards (the entire two-step pattern exists for this purpose).
- Key compromise of a stateGuard EOA, or a malicious stateGuard contract, is a realistic operational risk.
- The front-running window between `setStateGuardRole` and `acceptStateGuardRole` is open for an entire block (or longer on low-activity chains), making the race trivially exploitable by a monitoring stateGuard.
- No timelock, no admin escape hatch, no multisig requirement on the stateGuard role.

---

### Recommendation

Replace the two-step pattern with an `ADMIN_ROLE`-only direct setter (mirroring Compound's resolution):

```solidity
// ADMIN_ROLE can forcibly set the stateGuard for any feed, bypassing the two-step flow.
function forceSetStateGuard(bytes32 feedId, address newGuard) external onlyRole(ADMIN_ROLE) {
    address previous = stateGuard[feedId];
    stateGuard[feedId] = newGuard;
    delete pendingStateGuard[feedId];
    emit StateGuardUpdated(feedId, newGuard);
}
```

Additionally, remove the stateGuard's ability to overwrite `pendingStateGuard` while a pending transfer is in flight, or require a timelock on `setStateGuardRole` so the admin can cancel before the nominee accepts.

---

### Proof of Concept

```
1. ADMIN_ROLE calls setStateGuardRole(feedId, honestGuard)
   → pendingStateGuard[feedId] = honestGuard

2. Malicious stateGuard (current) front-runs honestGuard's acceptStateGuardRole:
   → stateGuard.setStateGuardRole(feedId, attackerAddress)
   → pendingStateGuard[feedId] = attackerAddress  (overwrites honestGuard)

3. attackerAddress calls acceptStateGuardRole(feedId)
   → stateGuard[feedId] = attackerAddress

4. ADMIN_ROLE attempts setStateGuardRole(feedId, newHonestGuard)
   → checkRole: stateGuard[feedId] = attackerAddress ≠ ADMIN_ROLE → REVERT

5. Attacker calls setPriceGuard(feedId, 1, 2)
   → priceGuard[feedId] = {min:1, max:2}
   → All future price pushes outside [1,2] are rejected
   → Oracle becomes stale
   → Pools execute swaps at stale price → bad-price execution / fund loss
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

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L99-103)
```text
    function setStateGuardRole(bytes32 feedId, address newGuard) external checkRole(feedId) {
        pendingStateGuard[feedId] = newGuard;

        emit StateGuardPending(feedId, newGuard);
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
