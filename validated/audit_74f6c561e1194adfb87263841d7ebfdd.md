### Title
`DepositAllowlistExtension` validates `owner` instead of `sender`, allowing any unprivileged caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` is documented as gating `addLiquidity` **by depositor address**, but the implementation silently discards the `sender` argument and checks `owner` instead. Because `owner` is a caller-supplied parameter in `MetricOmmPool.addLiquidity`, any address can bypass the allowlist by nominating an already-authorized address as the position owner.

---

### Finding Description

`MetricOmmPool.addLiquidity` calls the extension hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

Inside `ExtensionCalling._beforeAddLiquidity`, this becomes:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

The extension receives `(address sender, address owner, ...)`. The `sender` slot is the actual `msg.sender` of the pool call — the address that will supply tokens via the liquidity callback. The `owner` slot is a free parameter chosen by the caller.

`DepositAllowlistExtension.beforeAddLiquidity` drops `sender` entirely (unnamed) and gates only on `owner`:

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

The contract's own NatSpec states: *"Gates `addLiquidity` by depositor address, per pool."* The depositor is the entity that provides tokens (the `sender`/`msg.sender` of the pool call), not the position owner. The check is on the wrong address.

---

### Impact Explanation

An unauthorized address **Bob** calls:

```
pool.addLiquidity(owner = Alice, salt, deltas, callbackData, extensionData)
```

where `Alice` is an address already present in `allowedDepositor[pool][Alice]`. The extension sees `owner = Alice`, passes the check, and the pool proceeds. Bob supplies tokens through the liquidity callback and Alice receives the position. The pool admin's intent — restricting which addresses may deposit capital — is completely defeated. Any address can deposit by nominating any authorized address as `owner`.

This breaks the admin-configured allowlist boundary: an unprivileged, unauthorized path bypasses a pool-admin security control without any privileged action.

---

### Likelihood Explanation

The bypass requires only a single public call to `addLiquidity` with a known authorized address as `owner`. No special permissions, flash loans, or oracle manipulation are needed. Any address that can observe the allowlist (public mapping `allowedDepositor`) can execute the bypass immediately.

---

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
```

This aligns the check with the documented intent ("by depositor address") and with the analogous `SwapAllowlistExtension`, which correctly gates on `sender`:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
``` [4](#0-3) 

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` configured; `allowedDepositor[pool][Alice] = true`; Bob is **not** on the allowlist.
2. Bob deploys a contract implementing the liquidity callback that transfers the required tokens.
3. Bob calls `pool.addLiquidity(owner = Alice, salt = 0, deltas, callbackData, extensionData)`.
4. `beforeAddLiquidity` is invoked with `sender = Bob`, `owner = Alice`. The check evaluates `allowedDepositor[pool][Alice]` → `true`. No revert.
5. Bob's callback transfers tokens; Alice receives the liquidity position.
6. The deposit allowlist has been bypassed without any privileged action. [5](#0-4) [6](#0-5)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L12-42)
```text
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;

  constructor(address factory_) BaseMetricExtension(factory_) {}

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-38)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
```
