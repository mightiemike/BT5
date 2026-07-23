### Title
`DepositAllowlistExtension.beforeAddLiquidity` gates the wrong actor (`owner` instead of `sender`), allowing any unprivileged address to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**. Its `beforeAddLiquidity` hook ignores the `sender` argument (the actual `msg.sender` of `addLiquidity`) and instead checks the caller-supplied `owner` parameter. Because `MetricOmmPool.addLiquidity` accepts any `owner` address without requiring `msg.sender == owner`, any unprivileged address can bypass the allowlist by passing an already-authorized address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct actors to the extension hook: [1](#0-0) 

```
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
(amount0Added, amount1Added) = LiquidityLib.addLiquidity(
    _liquidityContext(), owner, salt, deltas, callbackData, ...
);
```

- `sender` = `msg.sender` — the address that actually calls the pool and provides tokens via the swap callback.
- `owner` = a **caller-supplied** parameter — the address that will receive LP shares; there is no `require(msg.sender == owner)` guard anywhere in `addLiquidity`. [2](#0-1) 

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both actors to the extension: [3](#0-2) 

`DepositAllowlistExtension.beforeAddLiquidity` then **silently discards `sender`** (unnamed first parameter) and checks only `owner`: [4](#0-3) 

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The allowlist mapping is keyed `allowedDepositor[pool][owner]`. Because `owner` is freely chosen by the caller, any address can pass the guard by supplying an already-authorized address as `owner`. The extension's own NatSpec and setter name (`setAllowedToDeposit`, parameter named `depositor`) confirm the intended subject is the depositing actor (`sender`), not the LP-share recipient. [5](#0-4) 

---

### Impact Explanation

A pool admin who deploys a `DepositAllowlistExtension` intends to restrict which addresses may inject liquidity. Because the guard checks the wrong actor, **any unprivileged address can deposit into a curated pool** by calling:

```
pool.addLiquidity(authorizedAddress, salt, deltas, callbackData, extensionData)
```

The check resolves `allowedDepositor[pool][authorizedAddress] == true` and passes. The unauthorized caller's tokens are pulled via the callback; LP shares are credited to `authorizedAddress`. The pool's curation invariant — that only allowlisted addresses may deposit — is fully broken. This constitutes an admin-boundary break: an unprivileged path bypasses a configured access-control guard on a production pool.

---

### Likelihood Explanation

The bypass requires only a standard `addLiquidity` call with a known authorized address as `owner`. No special permissions, flash loans, or multi-step setup are needed. Any address that can observe the allowlist (public mapping `allowedDepositor`) can execute the bypass immediately. Likelihood is **high**.

---

### Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual depositing address) instead of `owner`:

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

This aligns the guard with the economic actor (the address providing tokens) and matches the documented intent of the extension. [4](#0-3) 

---

### Proof of Concept

```solidity
// Setup: pool with DepositAllowlistExtension; only `authorizedUser` is allowed.
// extension.setAllowedToDeposit(pool, authorizedUser, true);

// Attacker (not on allowlist) bypasses the guard:
vm.prank(attacker);
pool.addLiquidity(
    authorizedUser,   // <-- authorized owner; guard checks this, not msg.sender
    salt,
    deltas,
    callbackData,
    extensionData
);
// Result: check passes, attacker's tokens are deposited, LP shares go to authorizedUser.
// The curated pool has accepted a deposit from an address the admin explicitly did not allowlist.
```

The `DepositAllowlistExtension` test suite confirms the extension only ever checks the `owner` slot and never validates `sender`: [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L12-20)
```text
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
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

**File:** metric-periphery/test/extensions/DepositAllowlistSubExtension.t.sol (L27-41)
```text
  function test_revertsWhenDepositorNotAllowed() public {
    vm.prank(address(pool));
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToDeposit.selector);
    LiquidityDelta memory emptyDelta = LiquidityDelta({binIdxs: new int256[](0), shares: new uint256[](0)});
    extension.beforeAddLiquidity(address(0), depositor, 0, emptyDelta, "");
  }

  function test_passesWhenDepositorAllowed() public {
    vm.prank(admin);
    extension.setAllowedToDeposit(address(pool), depositor, true);

    vm.prank(address(pool));
    LiquidityDelta memory emptyDelta = LiquidityDelta({binIdxs: new int256[](0), shares: new uint256[](0)});
    extension.beforeAddLiquidity(address(0), depositor, 0, emptyDelta, "");
  }
```
