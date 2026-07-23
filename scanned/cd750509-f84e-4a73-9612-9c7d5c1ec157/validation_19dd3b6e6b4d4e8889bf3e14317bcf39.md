### Title
Deposit Allowlist Checks `owner` Instead of `sender`, Allowing Any Caller to Bypass the Guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` enforces the allowlist against the `owner` argument (the position owner) rather than the `sender` argument (the actual `msg.sender` of `addLiquidity`). Because `owner` is a free caller-supplied parameter, any address — including one that is explicitly not on the allowlist — can bypass the guard by naming an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts an arbitrary `owner` address and passes both `msg.sender` (as `sender`) and `owner` to the extension hook: [1](#0-0) 

The extension receives `(sender, owner, ...)` in that order. `DepositAllowlistExtension.beforeAddLiquidity` silently drops `sender` (the first `address` parameter is unnamed) and gates only on `owner`: [2](#0-1) 

The check reads `allowedDepositor[msg.sender][owner]`, where `msg.sender` is the pool and `owner` is the caller-supplied position owner. Because `owner` is not validated against the actual caller anywhere in the pool before the hook fires, a non-allowlisted address can pass any allowlisted address as `owner` and the guard passes.

Compare with `SwapAllowlistExtension.beforeSwap`, which correctly gates on `sender` (the actual swap caller): [3](#0-2) 

The asymmetry confirms the deposit extension is checking the wrong parameter.

---

### Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for restricting who may add liquidity (e.g., KYC/AML gating, whitelist-only pools). With this bug the guard is entirely ineffective: any address can deposit by supplying an allowlisted address as `owner`. The position is minted to that `owner`, and the callback pulls tokens from the actual caller. The allowlist invariant — "only approved depositors may add liquidity" — is broken for every pool that deploys this extension. This is an admin-boundary break where an unprivileged path bypasses a factory-configured role check.

---

### Likelihood Explanation

The bypass requires only a single `addLiquidity` call with a known allowlisted address as `owner`. No special token, no privileged role, no complex setup. Any address that can observe the allowlist state (public mappings) can exploit it immediately.

---

### Recommendation

Replace the unnamed first parameter with `sender` and gate on it, mirroring `SwapAllowlistExtension`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [2](#0-1) 

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` configured; `allowAllDepositors` is `false`.
2. Pool admin calls `setAllowedToDeposit(pool, Alice, true)` — only Alice is permitted.
3. Bob (not allowlisted) calls `pool.addLiquidity(owner = Alice, salt = 0, deltas, callbackData, "")`.
4. The pool calls `extension.beforeAddLiquidity(sender=Bob, owner=Alice, ...)`.
5. The extension evaluates `allowedDepositor[pool][Alice]` → `true` → no revert.
6. Liquidity is minted to position `(Alice, 0)`; Bob's callback pays the tokens.
7. Bob has deposited into a pool he is explicitly barred from, bypassing the allowlist entirely. [4](#0-3) [5](#0-4)

### Citations

**File:** metric-core/contracts/ExtensionCalling.sol (L107-128)
```text
    uint256 amount1Added,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      AFTER_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.afterAddLiquidity, (sender, owner, salt, deltas, amount0Added, amount1Added, extensionData)
      )
    );
  }

  function _beforeRemoveLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_REMOVE_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeRemoveLiquidity, (sender, owner, salt, deltas, extensionData))
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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
  }
```
