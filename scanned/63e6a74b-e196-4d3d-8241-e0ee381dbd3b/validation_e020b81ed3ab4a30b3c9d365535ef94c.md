### Title
`DepositAllowlistExtension` gates position owner instead of actual depositor, allowing allowlist bypass — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently ignores the `sender` argument (the actual caller/depositor) and checks `owner` (the position recipient) instead. Because any caller can freely set `owner` to any allowlisted address, an unprivileged user can bypass the deposit allowlist entirely by naming an allowlisted address as the position owner.

---

### Finding Description

The admin configures the allowlist via `setAllowedToDeposit`, whose parameter is explicitly named `depositor`, signalling the intent to gate the token sender: [1](#0-0) 

The hook that enforces this allowlist, however, discards the first parameter (`sender`) and checks the second (`owner`): [2](#0-1) 

`sender` (first, unnamed parameter) is the `msg.sender` of the pool's `addLiquidity` call — the actual depositor. `owner` is the position recipient, which any caller can set to any address, including allowlisted ones.

`ExtensionCalling._beforeAddLiquidity` passes both values correctly to the extension: [3](#0-2) 

The only validation on `owner` in the liquidity adder rejects only `address(0)`: [4](#0-3) 

**Attack path:**

1. Admin calls `setAllowedToDeposit(pool, alice, true)` — only Alice is allowlisted.
2. Bob (not allowlisted) calls either:
   - `pool.addLiquidity(alice, salt, deltas, ...)` directly, or
   - `liquidityAdder.addLiquidityExactShares(pool, alice, salt, deltas, ...)` through the periphery adder.
3. The pool dispatches `beforeAddLiquidity(bob, alice, ...)` to the extension.
4. The extension evaluates `allowedDepositor[pool][alice]` → `true`. Check passes.
5. Bob's tokens are deposited; Alice receives the LP position without consent.

The `addLiquidityExactShares` overload that accepts an explicit `owner` makes this trivially reachable from the supported periphery path: [5](#0-4) 

---

### Impact Explanation

The deposit allowlist — the pool admin's sole mechanism for restricting who can provide liquidity — is completely bypassed. Any unprivileged user can deposit into a curated (e.g., KYC-gated or private) pool by naming any allowlisted address as the position owner. The allowlisted address receives LP tokens without consent, creating a griefing vector. The pool's curation invariant is broken: the admin-configured protection is bypassed by a valid, unprivileged periphery call.

---

### Likelihood Explanation

The bypass requires a single transaction through a publicly callable function. No special privileges, flash loans, or multi-step setup are needed. Allowlisted addresses are publicly discoverable via `AllowedToDepositSet` events. Any pool deploying `DepositAllowlistExtension` with a non-empty allowlist is immediately vulnerable.

---

### Recommendation

Check `sender` (the actual depositor) instead of `owner` (the position recipient):

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

This ensures the address actually sending tokens is checked against the allowlist, not the position recipient.

---

### Proof of Concept

```solidity
// Assume pool is deployed with DepositAllowlistExtension.
// Admin allowlists only Alice:
extension.setAllowedToDeposit(pool, alice, true);

// Bob (not allowlisted) deposits by naming Alice as owner:
vm.prank(bob);
pool.addLiquidity(alice, salt, deltas, callbackData, "");
// → beforeAddLiquidity(bob, alice, ...) is called
// → allowedDepositor[pool][alice] == true → passes
// → Bob's tokens enter the pool; Alice receives LP shares

// Confirm Bob bypassed the allowlist:
assertGt(pool.positionShares(alice, salt, binIdx), 0); // Alice has shares
// Bob's token balance decreased — his tokens are in the pool
```

### Citations

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-21)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
  }
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

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
  }
```

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```
