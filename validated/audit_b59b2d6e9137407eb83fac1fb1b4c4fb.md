The code is unambiguous. Let me confirm the exact lines before rendering the verdict.

The `register` function in `smart-contracts-poc/contracts/oracles/providers/OracleBase.sol` is permissionless and explicitly clears the blacklist: [1](#0-0) 

The NatDoc at line 198 even documents this: *"Paying also clears any blacklist on the pool."*

The `setBlacklist` function requires `ADMIN_ROLE`: [2](#0-1) 

But `register` has **no role check** — only a fee check (default 1 wei), an approved-factory check, and an `isPool` check. Since the factory was already approved by ADMIN (a prerequisite for the pool to have been registered in the first place), and the pool is still recognized by `isPool()`, an attacker can satisfy all three conditions trivially.

The `price()` function enforces the blacklist at read time: [3](#0-2) 

Once `blacklisted[pool]` is cleared via `register`, this check passes and the pool resumes oracle reads and swaps.

---

### Title
Unprivileged caller can clear ADMIN blacklist via `register()` paying 1 wei — (`smart-contracts-poc/contracts/oracles/providers/OracleBase.sol`)

### Summary
`OracleBase.register` is a permissionless, payable function that unconditionally sets `blacklisted[pool] = false` whenever the pool is currently blacklisted. Because `setBlacklist` requires `ADMIN_ROLE` but `register` does not, any caller can pay the default 1 wei registration fee and restore oracle read access to a pool the ADMIN explicitly blacklisted.

### Finding Description
`OracleBase.register` (line 201) requires only:
1. `msg.value >= registrationFee` (default: 1 wei)
2. `factory` is in `approvedFactories` (ADMIN-controlled, but already satisfied for any live pool)
3. `IPoolFactory(factory).isPool(pool)` returns true

If `blacklisted[pool]` is `true`, lines 207–210 unconditionally set it to `false` and emit `BlacklistUpdated`. There is no role check, no timelock, and no additional authorization. The NatDoc comment at line 198 documents this as intentional ("Paying also clears any blacklist on the pool"), but the design directly contradicts the invariant that blacklist revocation requires `ADMIN_ROLE`. [4](#0-3) 

### Impact Explanation
The blacklist is the sole abuse-deterrent mechanism for oracle reads. Once cleared, `price(feedId, pool)` passes the `require(!blacklisted[pool])` check at line 167, and the pool resumes receiving oracle prices and executing swaps. If the pool was blacklisted due to price manipulation, sandwich abuse, or other on-chain misbehavior, the attacker restores the exact attack surface the ADMIN tried to close. This is a direct admin-boundary break enabling bad-price execution and potential fund loss.

### Likelihood Explanation
The attack requires only 1 wei and knowledge of any ADMIN-approved factory (publicly readable via `getApprovedFactories`). The pool address is known (it was blacklisted by a public `setBlacklist` transaction). No privileged access, no off-chain data, and no special token behavior is needed. Any EOA can execute this in a single transaction.

### Recommendation
Remove the blacklist-clearing logic from `register`. Blacklist revocation must remain exclusively in `setBlacklist` (ADMIN_ROLE). If re-registration of a previously blacklisted pool is desired, require explicit ADMIN authorization as a separate step before `register` is called, or add an explicit `require(!blacklisted[pool])` revert to `register`.

```solidity
// In register(), replace:
if (blacklisted[pool]) {
    blacklisted[pool] = false;
    emit BlacklistUpdated(pool, false);
}
// With:
require(!blacklisted[pool], Blacklisted(pool));
```

### Proof of Concept
```solidity
// Foundry test
function test_blacklistBypass() public {
    // ADMIN blacklists poolA
    vm.prank(admin);
    oracle.setBlacklist(poolA, true);
    assertTrue(oracle.blacklisted(poolA));

    // Attacker (any EOA) re-registers poolA paying 1 wei
    vm.deal(attacker, 1 wei);
    vm.prank(attacker);
    oracle.register{value: 1}(feedId, poolA, approvedFactory);

    // Blacklist is cleared
    assertFalse(oracle.blacklisted(poolA));

    // poolA can now read oracle price and execute swaps
    vm.prank(poolA_provider);
    (uint256 mid,,,) = oracle.price(feedId, poolA);
    // PriceRead event emitted — swap proceeds
}
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L166-168)
```text
        require(pool != address(0) && IPool(pool).inSwap() == msg.sender, InvalidInSwap());
        require(!blacklisted[pool], Blacklisted(pool));
        require(registeredPool[feedId][pool], NotRegistered(feedId, pool));
```

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L196-214)
```text
    /// @notice Permissionless paid registration: whitelist `pool` for `feedId` (required to use the
    ///         on-chain price(feedId, factory) path). `factory` must be approved and recognize `pool`
    ///         via isPool. Paying also clears any blacklist on the pool.
    /// @dev    Overpayment is NOT refunded: any msg.value above registrationFee is kept and is
    ///         withdrawable by ADMIN via withdrawEth. This is intentional.
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
