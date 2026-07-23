The vulnerability claim is valid. Here is the full trace and finding.

---

### Title
`DepositAllowlistExtension.beforeAddLiquidity` checks position `owner` instead of the token-funding `payer`, allowing non-allowlisted parties to deposit into curated pools — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` gates deposits by checking whether the LP position `owner` is allowlisted, but the actual token funder (`payer`) is a separate address stored in transient storage inside `MetricOmmPoolLiquidityAdder` and is never surfaced to the extension. An attacker who is not allowlisted can call `addLiquidityExactShares(pool, owner=allowlisted_user, ...)`, pass the allowlist check (because `owner` is allowlisted), and have their own tokens pulled into the pool.

---

### Finding Description

**Step 1 — Attacker calls the router with a different `owner`:**

`addLiquidityExactShares` accepts an explicit `owner` parameter. The only validation is `owner != address(0)`. The `payer` is always hardcoded to `msg.sender` (the attacker). [1](#0-0) 

**Step 2 — Router stores attacker as payer in transient storage, then calls `pool.addLiquidity(owner=allowlisted_user, ...)`:** [2](#0-1) 

**Step 3 — Pool calls `beforeAddLiquidity(caller, owner=allowlisted_user, ...)`; extension checks `allowedDepositor[pool][owner]`:**

The check is `allowedDepositor[msg.sender][owner]` where `msg.sender` is the pool and `owner` is the allowlisted user. This passes. The actual payer (attacker) is never checked — it is not a parameter of `beforeAddLiquidity` and the extension has no access to it. [3](#0-2) 

**Step 4 — Callback pulls tokens from attacker:**

After the allowlist check passes and LP shares are minted to `allowlisted_user`, the callback fires and pulls tokens from `payer` = attacker. [4](#0-3) 

---

### Impact Explanation

The `DepositAllowlistExtension` is designed to enforce pool curation: only approved depositors may fund a pool. This invariant is broken. A non-allowlisted address can:

1. Fund a curated pool with its own tokens.
2. Force LP shares onto an allowlisted user without their consent (griefing).
3. Circumvent any KYC/compliance or economic gating the pool admin intended.

The pool receives tokens from an unapproved source, directly violating the core functionality of the allowlist extension. This constitutes broken core pool functionality under the contest's impact gate.

---

### Likelihood Explanation

The attack path is fully permissionless. Any external address can call `addLiquidityExactShares` with an arbitrary `owner`. No privileged role, special token, or malicious pool setup is required. The only precondition is knowing an allowlisted address for the target pool, which is readable from `allowedDepositor` (public mapping). [5](#0-4) 

---

### Recommendation

`beforeAddLiquidity` must also verify the actual token funder. Two options:

1. **Pass the payer through `extensionData`**: The router encodes the payer into `extensionData` and the extension decodes and checks it against the allowlist.
2. **Restrict `owner == caller`**: Require that the first parameter (caller, i.e., the address that called `pool.addLiquidity`) equals `owner`, so the position owner and funder are always the same entity. The extension currently ignores the first parameter entirely. [3](#0-2) 

---

### Proof of Concept

```solidity
// Foundry integration test sketch
function test_allowlistBypass() public {
    address allowlistedUser = makeAddr("allowlisted");
    address attacker        = makeAddr("attacker");

    // Pool admin allowlists only allowlistedUser
    vm.prank(poolAdmin);
    depositAllowlistExt.setAllowedToDeposit(pool, allowlistedUser, true);

    // Attacker approves the LiquidityAdder for token pulls
    vm.startPrank(attacker);
    token0.approve(address(liquidityAdder), type(uint256).max);
    token1.approve(address(liquidityAdder), type(uint256).max);

    // Attacker calls with owner = allowlistedUser (attacker is NOT allowlisted)
    liquidityAdder.addLiquidityExactShares(
        pool, allowlistedUser, salt, deltas, max0, max1, ""
    );
    vm.stopPrank();

    // Assert: LP shares minted to allowlistedUser, tokens pulled from attacker
    assertGt(pool.balanceOf(allowlistedUser, ...), 0);
    assertLt(token0.balanceOf(attacker), initialBalance); // attacker funded the pool
}
```

The test will pass: `beforeAddLiquidity` sees `allowedDepositor[pool][allowlistedUser] == true` and does not revert, while the callback drains tokens from the attacker.

### Citations

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L162-177)
```text
    (address expectedPool, address payer, uint256 max0, uint256 max1) = _loadPayContext();
    if (expectedPool == address(0)) revert CallbackContextNotActive();
    if (msg.sender != expectedPool) revert InvalidCallbackCaller(msg.sender, expectedPool);
    if (amount0Delta > max0 || amount1Delta > max1) {
      revert MaxAmountExceeded(amount0Delta, amount1Delta, max0, max1);
    }

    PoolImmutables memory imm = IMetricOmmPool(msg.sender).getImmutables();
    address token0 = imm.token0;
    address token1 = imm.token1;
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
    }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L193-196)
```text
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L13-13)
```text
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
