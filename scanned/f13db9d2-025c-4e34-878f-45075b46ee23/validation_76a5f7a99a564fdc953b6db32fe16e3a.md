### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Address to Bypass the Deposit Guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` is documented as gating `addLiquidity` **by depositor address**, but the implementation checks the LP position `owner` parameter instead of the `sender` (the actual caller paying tokens). Any unprivileged address can bypass the allowlist by specifying any allowlisted address as the `owner` of the position.

---

### Finding Description

`ExtensionCalling._beforeAddLiquidity` forwards two distinct actor addresses to the extension hook:

- `sender` — the address that called `pool.addLiquidity` (the actual depositor/payer)
- `owner` — the address that will own the resulting LP position (may differ from `sender`) [1](#0-0) 

The extension receives both, but silently drops `sender` (named `address` with no binding) and evaluates only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [2](#0-1) 

`MetricOmmPoolLiquidityAdder` explicitly supports adding liquidity on behalf of a separate `owner`: [3](#0-2) 

Because the check is `allowedDepositor[pool][owner]` rather than `allowedDepositor[pool][sender]`, any address — including one explicitly excluded from the allowlist — can call `pool.addLiquidity(owner = allowlisted_address, ...)` and the guard passes.

The `SwapAllowlistExtension` does not share this flaw; it correctly checks `sender`: [4](#0-3) 

---

### Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for restricting who may add liquidity to a pool (e.g., KYC-gated pools, private liquidity programs, or pools that must remain closed to the public). With the guard checking the wrong actor:

1. **Allowlist bypass**: Any address not on the allowlist can deposit tokens into a restricted pool by naming any allowlisted address as `owner`. The unauthorized depositor pays the tokens; the allowlisted address receives the LP position.
2. **Pool state manipulation**: An attacker can add liquidity to specific bins, shifting `curPosInBin` / `curBinIdx` state and affecting subsequent swap pricing — harming existing LPs.
3. **Authorized depositor blocked**: Conversely, an allowlisted depositor using `MetricOmmPoolLiquidityAdder` to credit a position to a non-allowlisted `owner` is incorrectly rejected, breaking the intended "payer ≠ owner" use case.

This is a direct admin-boundary break: the pool admin's configured access control is bypassed by an unprivileged path.

---

### Likelihood Explanation

The `MetricOmmPoolLiquidityAdder` explicitly advertises and tests the `owner ≠ sender` pattern. Any attacker who reads the periphery documentation or tests will immediately discover that specifying an allowlisted address as `owner` bypasses the guard. No special privileges, flash loans, or oracle manipulation are required — a single direct call to `pool.addLiquidity` suffices. [5](#0-4) 

---

### Recommendation

Replace the ignored first parameter with `sender` and check it instead of (or in addition to) `owner`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intent is to gate both the depositor and the position owner, both should be checked. The `setAllowedToDeposit` admin setter and `isAllowedToDeposit` view should be reviewed to confirm which actor the pool admin intends to control. [6](#0-5) 

---

### Proof of Concept

```
Setup:
  - Pool deployed with DepositAllowlistExtension registered on BEFORE_ADD_LIQUIDITY_ORDER
  - Pool admin calls setAllowedToDeposit(pool, alice, true)   // alice is allowlisted
  - Pool admin does NOT allowlist bob

Attack:
  1. bob calls pool.addLiquidity(owner = alice, salt = 1, deltas = [...])
     → ExtensionCalling._beforeAddLiquidity(sender=bob, owner=alice, ...)
     → DepositAllowlistExtension.beforeAddLiquidity(_, alice, ...)
     → checks allowedDepositor[pool][alice] == true  ✓  (passes)
  2. bob's tokens are transferred to the pool; alice's LP position is credited.
  3. The allowlist guard never evaluated bob's address.

Result: bob, an address the pool admin explicitly did not allowlist, successfully
deposits into the restricted pool. The pool admin's access control is silently bypassed.
```

### Citations

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L12-14)
```text
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-30)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
  }

  function setAllowAllDepositors(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllDepositors[pool_] = allowed;
    emit AllowAllDepositorsSet(pool_, allowed);
  }

  function isAllowedToDeposit(address pool_, address depositor) external view returns (bool) {
    return allowAllDepositors[pool_] || allowedDepositor[pool_][depositor];
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```
