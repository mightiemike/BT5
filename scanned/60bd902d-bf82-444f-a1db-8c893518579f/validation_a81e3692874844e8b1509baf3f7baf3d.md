### Title
Permissionless `register()` Unconditionally Clears Admin Blacklist, Allowing Any Caller to Reinstate a Blacklisted Pool's Oracle Price Access — (`smart-contracts-poc/contracts/oracles/providers/OracleBase.sol`)

### Summary

`OracleBase.register()` is a permissionless, payable function that explicitly resets `blacklisted[pool]` to `false` for any pool that passes the `isPool` factory check. Because the default `registrationFee` is 1 wei and there is no access control on `register()`, any caller — including the pool owner — can trivially undo an admin blacklist decision, restoring the pool's ability to call `price()` and receive oracle-derived bid/ask quotes.

### Finding Description

`setBlacklist` is correctly gated to `ADMIN_ROLE`: [1](#0-0) 

However, `register()` carries no access control and explicitly clears the flag: [2](#0-1) 

The NatSpec on line 198 even documents this: *"Paying also clears any blacklist on the pool."* The default fee is 1 wei: [3](#0-2) 

The only gate between `register()` and blacklist-clearing is:
1. `msg.value >= registrationFee` (1 wei by default)
2. `approvedFactories.contains(factory)` — factory approval is admin-controlled but applies globally to all pools from that factory
3. `IPoolFactory(factory).isPool(pool)` — the pool must be a real pool from an approved factory

A blacklisted pool is, by definition, a real pool from an approved factory (it had to pass these checks to be registered in the first place). So all three conditions are trivially satisfiable by the pool owner or any third party.

After `register()` succeeds, `blacklisted[pool]` is `false` and `registeredPool[feedId][pool]` is `true`, so `price()` will succeed: [4](#0-3) 

### Impact Explanation

The blacklist is the oracle's primary abuse-protection mechanism. If a pool is blacklisted (e.g., due to price manipulation, flash-loan abuse, or protocol-level compromise), the admin's decision is immediately reversible by anyone for 1 wei. The pool regains access to oracle-derived `mid`, `spread`, and `spread1` values used to price swaps, directly enabling bad-price execution or continued abuse that the blacklist was intended to stop. This is an admin-boundary break: an unprivileged path overrides an explicit admin security decision with real fund-impact consequences (oracle-priced swaps executing against a pool the admin deemed unsafe).

### Likelihood Explanation

Exploitation requires only that the pool is recognized by an approved factory (guaranteed for any legitimately deployed pool) and payment of 1 wei. The pool owner has a direct financial incentive to re-register immediately after being blacklisted. No special permissions, flash loans, or off-chain data are needed.

### Recommendation

Add a hard blacklist check at the top of `register()` that reverts if the pool is blacklisted, making blacklisting irrevocable through the public registration path:

```solidity
function register(bytes32 feedId, address pool, address factory) external payable {
    require(!blacklisted[pool], Blacklisted(pool)); // <-- add this
    require(msg.value >= registrationFee, InsufficientFee(msg.value, registrationFee));
    ...
}
```

If the intended design is that paying the fee rehabilitates a pool, then `setBlacklist` must be removed or the blacklist must be documented as non-binding, and the abuse-protection model must be redesigned accordingly.

### Proof of Concept

```solidity
// Foundry test
function test_blacklistBypass() public {
    // Admin blacklists the pool
    vm.prank(admin);
    oracle.setBlacklist(pool, true);
    assertTrue(oracle.blacklisted(pool));

    // Anyone pays 1 wei to re-register and clear the blacklist
    vm.deal(attacker, 1 wei);
    vm.prank(attacker);
    oracle.register{value: 1 wei}(feedId, pool, approvedFactory);

    // Blacklist is now cleared
    assertFalse(oracle.blacklisted(pool));

    // Pool can now read oracle prices again
    vm.prank(address(priceProvider));
    (uint256 mid,,,) = oracle.price(feedId, pool);
    assertGt(mid, 0);
}
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L53-53)
```text
        registrationFee = 1 wei; // very cheap default; ADMIN tunes via setRegistrationFee
```

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L160-172)
```text
    function price(bytes32 feedId, address pool)
        external
        feedExists(feedId)
        notBlacklisted
        returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
    {
        require(pool != address(0) && IPool(pool).inSwap() == msg.sender, InvalidInSwap());
        require(!blacklisted[pool], Blacklisted(pool));
        require(registeredPool[feedId][pool], NotRegistered(feedId, pool));

        (mid, spread, spread1, refTime) = _readPrice(feedId);
        emit PriceRead(pool, feedId);
    }
```

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L201-214)
```text
    function register(bytes32 feedId, address pool, address factory) external payable {
        require(msg.value >= registrationFee, InsufficientFee(msg.value, registrationFee));
        require(pool != address(0));
        require(approvedFactories.contains(factory), FactoryNotApproved(factory));
        require(IPoolFactory(factory).isPool(pool), NotAPool(pool));

        if (blacklisted[pool]) {
            blacklisted[pool] = false;
            emit BlacklistUpdated(pool, false);
        }

        registeredPool[feedId][pool] = true;
        emit PoolRegistered(feedId, pool, msg.sender, msg.value);
    }
```

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L271-276)
```text
    function setBlacklist(address account, bool value) external onlyRole(ADMIN_ROLE) {
        require(account != address(0));
        if (blacklisted[account] == value) return;
        blacklisted[account] = value;
        emit BlacklistUpdated(account, value);
    }
```
