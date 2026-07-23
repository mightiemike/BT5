Looking at the extension system, I need to trace how `sender` and `owner` flow through the hook plumbing and whether the allowlist guards check the correct actor.

### Title
`DepositAllowlistExtension` Silently Discards `sender` and Checks Caller-Controlled `owner`, Allowing Any Address to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

The `DepositAllowlistExtension.beforeAddLiquidity` hook validates the `owner` parameter (the LP position owner) against the per-pool allowlist, but silently discards the `sender` parameter (the actual caller providing tokens). Because `owner` is a free caller-supplied argument to `MetricOmmPool.addLiquidity`, any unprivileged address can bypass the deposit allowlist entirely by naming an allowlisted address as `owner`.

---

### Finding Description

**The hook ignores `sender` and checks only `owner`:**

In `DepositAllowlistExtension.beforeAddLiquidity`, the first parameter (`sender`) is unnamed and discarded:

```solidity
// DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [1](#0-0) 

**`owner` is a free caller-supplied argument in the pool:**

`MetricOmmPool.addLiquidity` accepts `owner` as an arbitrary external parameter and passes it verbatim to the hook:

```solidity
// MetricOmmPool.sol L182-195
function addLiquidity(
    address owner,          // ← caller-controlled
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (...) {
    ...
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [2](#0-1) 

**The hook dispatch passes both `sender` and `owner` correctly — the bug is in the extension:**

`ExtensionCalling._beforeAddLiquidity` faithfully forwards `(sender, owner, ...)` to the extension:

```solidity
// ExtensionCalling.sol L88-99
function _beforeAddLiquidity(address sender, address owner, ...) internal {
    _callExtensionsInOrder(
        BEFORE_ADD_LIQUIDITY_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
}
``` [3](#0-2) 

The extension receives `sender` as its first argument but drops it. The guard only ever sees `owner`.

**Contrast with `SwapAllowlistExtension`, which correctly checks `sender`:**

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
``` [4](#0-3) 

The swap guard checks the actual caller (`sender`). The deposit guard checks the position owner (`owner`). This asymmetry is the root cause.

---

### Impact Explanation

The pool pulls tokens from `msg.sender` (the actual caller) via the callback mechanism (`callbackData`). The `owner` only receives the resulting LP shares. Therefore:

- An unauthorized address (`bob`) calls `pool.addLiquidity(owner = alice, ...)` where `alice` is allowlisted.
- The extension checks `alice` → passes.
- The pool calls back to `bob` to pull tokens.
- `alice` receives LP shares; `bob` has deposited into a pool that was supposed to reject them.

The deposit allowlist — the pool admin's primary mechanism for running a permissioned liquidity pool — is fully bypassed by any unprivileged caller. Unauthorized capital enters the pool, violating the invariant that only approved addresses can provide liquidity. This is a direct admin-boundary break: an unprivileged path circumvents the pool admin's configured access control with no special role or privilege required.

---

### Likelihood Explanation

Exploitation requires only a standard `addLiquidity` call with `owner` set to any address already on the allowlist. No flash loans, no reentrancy, no oracle manipulation. Any address that can observe the allowlist (public mappings) can execute the bypass in a single transaction.

---

### Recommendation

Replace the `owner` check with a `sender` check (or check both), consistent with how `SwapAllowlistExtension` operates:

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intent is to gate both the token provider and the position owner, check both `sender` and `owner`.

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` configured; only `alice` is allowlisted via `setAllowedToDeposit(pool, alice, true)`.
2. `bob` (not allowlisted) calls `pool.addLiquidity(owner=alice, salt=0, deltas=..., callbackData=..., extensionData=...)`.
3. Pool calls `_beforeAddLiquidity(sender=bob, owner=alice, ...)`.
4. `DepositAllowlistExtension` evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
5. Pool executes liquidity addition; callback fires on `bob`, pulling `bob`'s tokens

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L182-195)
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
