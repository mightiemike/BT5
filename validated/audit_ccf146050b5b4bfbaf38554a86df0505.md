### Title
`DepositAllowlistExtension` checks position `owner` instead of transaction `sender`, allowing any address to bypass the deposit allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently ignores the `sender` argument (the actual caller of `addLiquidity`) and gates on `owner` (the position recipient). Because `addLiquidity` lets the caller freely choose any `owner`, any unprivileged address can bypass the allowlist entirely by naming an allowlisted address as the position owner.

### Finding Description

`MetricOmmPool.addLiquidity` calls the extension hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

The first argument is `msg.sender` — the actual depositor — and the second is the caller-supplied `owner` parameter. Inside `ExtensionCalling._beforeAddLiquidity` this maps to the `sender` and `owner` positions of `IMetricOmmExtensions.beforeAddLiquidity`. [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives both values but discards `sender` (left unnamed) and checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [3](#0-2) 

Because `addLiquidity` imposes no restriction on who may be named as `owner`, any caller can pass an allowlisted address as `owner` and the guard returns success without ever inspecting the actual depositor. The pool then records the position under `owner` while the callback charges tokens from `msg.sender`. [4](#0-3) 

The allowlist management functions (`setAllowedToDeposit`, `setAllowAllDepositors`) and the view `isAllowedToDeposit` all operate on the depositor dimension, confirming the intent is to gate the actual depositing address, not the position recipient. [5](#0-4) 

### Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for restricting which addresses may provide liquidity (e.g., KYC/AML compliance, institutional-only pools). With this bug the guard is completely inoperative: any unprivileged address deposits freely by naming any allowlisted address as `owner`. The allowlisted address receives an unsolicited LP position it did not authorise, which exposes it to pool risk (impermanent loss, fee exposure, oracle-driven value changes) without consent. The pool admin's configured access boundary is bypassed by a zero-privilege path, satisfying the "admin-boundary break" impact criterion.

### Likelihood Explanation

Exploitation requires only a standard `addLiquidity` call with a publicly observable allowlisted address as `owner`. No special role, flash loan, or oracle manipulation is needed. Any address can execute this at any time the pool is active.

### Recommendation

Replace the unnamed first parameter with `sender` and gate on it instead of `owner`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [3](#0-2) 

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` and calls `setAllowedToDeposit(pool, alice, true)`. Bob is not allowlisted.
2. Bob calls `pool.addLiquidity(owner = alice, salt, deltas, callbackData, extensionData)`.
3. The pool calls `_beforeAddLiquidity(sender = bob, owner = alice, ...)`.
4. `DepositAllowlistExtension.beforeAddLiquidity` evaluates `allowedDepositor[pool][alice]` → `true`; the guard passes.
5. `LiquidityLib.addLiquidity` records the position under `alice`; the swap callback charges tokens from Bob.
6. Bob has deposited into a pool he is not authorised to enter. Alice holds an LP position she never requested, bearing all associated pool risk.

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-29)
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
