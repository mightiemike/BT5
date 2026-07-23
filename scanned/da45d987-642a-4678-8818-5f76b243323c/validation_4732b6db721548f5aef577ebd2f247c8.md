### Title
`checkRole` Modifier in Compressed `OracleBase` Falls Back to `address(0)` Instead of `ADMIN_ROLE`, Permanently Locking Out Price Guard Configuration — (File: `smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol`)

---

### Summary

The `checkRole` modifier in the compressed `OracleBase` resolves the fallback authority for a feed with no explicit `stateGuard` by calling `_defaultGuard()`, which unconditionally returns `address(0)`. The resulting check `require(address(0) == msg.sender)` can never pass, so `setPriceGuard()` and `setPendingStateGuardRole()` are permanently uncallable for every feed that has not yet been assigned an explicit guard. Because `stateGuard[feedId]` starts as `address(0)` for all feeds and can only be set through `setPendingStateGuardRole()` (which itself requires `checkRole`), the guard system is permanently broken for all feeds in the compressed oracle. The providers-layer `OracleBase` does not share this defect: it falls back to `_checkRole(ADMIN_ROLE)` when no stateGuard is set.

---

### Finding Description

**Compressed `OracleBase` — wrong fallback authority**

```solidity
// smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol
modifier checkRole(bytes32 feedId) {
    address guard = stateGuard[feedId];
    if (guard == address(0)) guard = _defaultGuard(feedId);   // ← always address(0)
    require(guard == msg.sender, InvalidGuard(msg.sender));    // ← always reverts
    _;
}

function _defaultGuard(bytes32) internal view virtual returns (address) {
    return address(0);   // ← no override exists; base always returns zero
}
``` [1](#0-0) 

Every feed starts with `stateGuard[feedId] == address(0)`. The modifier substitutes `_defaultGuard()` for the missing guard, but `_defaultGuard()` returns `address(0)`. The `require` then demands `msg.sender == address(0)`, which is impossible. Both guarded functions are therefore permanently inaccessible:

```solidity
function setPriceGuard(bytes32 feedId, uint128 minPrice, uint128 maxPrice)
    external checkRole(feedId) { ... }          // unreachable for any feed

function setPendingStateGuardRole(bytes32 feedId, address newGuard)
    external checkRole(feedId) { ... }          // unreachable → stateGuard can never be set
``` [2](#0-1) 

Because `setPendingStateGuardRole` is also gated by `checkRole`, there is no bootstrap path: the stateGuard can never be advanced from `address(0)`, so the lockout is permanent and self-reinforcing.

**Contrast with the providers-layer `OracleBase`** (the correct pattern):

```solidity
// smart-contracts-poc/contracts/oracles/providers/OracleBase.sol
modifier checkRole(bytes32 feedId) {
    address _guard = stateGuard[feedId];
    if (_guard != address(0)) {
        require(_guard == msg.sender, InvalidGuard(msg.sender));
    } else {
        _checkRole(ADMIN_ROLE);   // ← correct: ADMIN is the fallback authority
    }
    _;
}
``` [3](#0-2) 

The providers-layer version correctly falls back to `ADMIN_ROLE` when no stateGuard is set, matching the intended design. The compressed-layer version omits this fallback entirely.

---

### Impact Explanation

`setPriceGuard` is the sole mechanism for bounding the min/max price that the compressed oracle will accept and propagate. With it permanently uncallable:

1. **No price bounds can ever be installed** on any feed served by the compressed oracle.
2. An oracle pusher (or a compromised/malicious one) can push arbitrarily extreme prices with no on-chain rejection.
3. Those unbounded prices flow directly into pool swaps via the `IPriceProvider` → `MetricOmmPool` path, producing bad-price execution: traders receive more than the oracle/bin curve permits, or LPs absorb losses from mispriced swaps.

This matches the allowed impact categories **"Bad-price execution: unbounded bid/ask quote reaches a pool swap"** and **"Swap conservation failure."**

---

### Likelihood Explanation

- The defect is present from deployment; no special trigger is required.
- Every feed in the compressed oracle is affected from block 0 (stateGuard starts at `address(0)` for all feeds).
- The ADMIN cannot remediate it without a contract upgrade, because the only remediation path (`setPendingStateGuardRole`) is itself blocked by the same broken modifier.
- Exploitation requires a compromised or malicious oracle pusher, making this Medium rather than Critical, but the protective mechanism is structurally absent rather than merely weak.

---

### Recommendation

Replace `_defaultGuard()` in the compressed `OracleBase` with the same fallback pattern used by the providers-layer `OracleBase`:

```solidity
modifier checkRole(bytes32 feedId) {
    address guard = stateGuard[feedId];
    if (guard != address(0)) {
        require(guard == msg.sender, InvalidGuard(msg.sender));
    } else {
        _checkRole(ADMIN_ROLE);   // ADMIN is the authority before an explicit guard is set
    }
    _;
}
```

Alternatively, override `_defaultGuard` in every concrete compressed-oracle subclass to return the deployer/admin address, but the explicit `_checkRole(ADMIN_ROLE)` branch is safer and consistent with the providers-layer design.

---

### Proof of Concept

```solidity
// Demonstrates permanent lockout in the compressed OracleBase

CompressedOracle oracle = new CompressedOracle(admin, maxDrift);

bytes32 feedId = keccak256("ETH/USD");

// stateGuard[feedId] == address(0) at deployment

// ADMIN attempts to set a price guard — reverts with InvalidGuard(admin)
vm.prank(admin);
oracle.setPriceGuard(feedId, 1e8, 1e12);
// ↑ reverts: checkRole resolves guard = _defaultGuard() = address(0)
//            require(address(0) == admin) → false → InvalidGuard(admin)

// ADMIN attempts to bootstrap a stateGuard — also reverts
vm.prank(admin);
oracle.setPendingStateGuardRole(feedId, admin);
// ↑ same revert: checkRole blocks this too

// Result: price guard is permanently unconfigurable;
// oracle pusher can push any price with no on-chain rejection.
``` [4](#0-3)

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol (L31-60)
```text
    modifier checkRole(bytes32 feedId) {
        address guard = stateGuard[feedId];
        if (guard == address(0)) guard = _defaultGuard(feedId);
        require(guard == msg.sender, InvalidGuard(msg.sender));
        _;
    }

    /// The authority a feed falls back to before an explicit stateGuard is accepted.
    function _defaultGuard(bytes32) internal view virtual returns (address) {
        return address(0);
    }

    /*
     *
     * Service functions
     *
     */

    function setPriceGuard(bytes32 feedId, uint128 minPrice, uint128 maxPrice)
        external
        checkRole(feedId)
    {
        require(minPrice < maxPrice);

        priceGuard[feedId] = PriceGuard({min: minPrice, max: maxPrice});

        emit PriceGuardUpdated(feedId, minPrice, maxPrice);
    }

    function setPendingStateGuardRole(bytes32 feedId, address newGuard) external checkRole(feedId) {
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
