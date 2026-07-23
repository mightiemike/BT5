Looking at the `DepositAllowlistExtension`, I can identify a clear analog to the M-02 bug class: a guard that checks the wrong entity, allowing it to be bypassed through a caller-controlled parameter.

### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` validates the caller-supplied `owner` parameter against the allowlist instead of the actual transaction initiator (`sender`). Because `owner` is a free parameter in `MetricOmmPool.addLiquidity`, any unprivileged address can bypass the allowlist entirely by setting `owner` to any address that is already on the allowlist.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` address and passes it, alongside `msg.sender` as `sender`, into the extension hook: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` then encodes both `sender` and `owner` and dispatches them to the configured extension: [2](#0-1) 

Inside `DepositAllowlistExtension.beforeAddLiquidity`, the first parameter (`sender`, the actual transaction initiator) is silently discarded (unnamed `address`), and the check is performed exclusively on `owner`: [3](#0-2) 

Because `owner` is freely chosen by the caller, any address can pass the guard by supplying an `owner` that is already on the allowlist. The `SwapAllowlistExtension` correctly checks `sender` for comparison: [4](#0-3) 

This inconsistency confirms the `DepositAllowlistExtension` is checking the wrong entity.

---

### Impact Explanation

The deposit allowlist is completely neutralized. Any unprivileged caller can add liquidity to a restricted pool by setting `owner` to any address that is on the allowlist. The position (shares) is credited to that `owner`, and the caller provides the tokens via the `addLiquidityCallback`. The pool admin's intent to restrict participation (e.g., for KYC compliance, private pools, or regulatory requirements) is fully defeated. This is an admin-boundary break where an unprivileged path bypasses a required hook.

Additionally, the bypass enables griefing: an unauthorized caller can forcibly add liquidity to any allowed address's position (keyed by `owner + salt`) without that address's consent, modifying their LP position in ways they did not authorize.

---

### Likelihood Explanation

Exploitation requires no special conditions, no privileged access, and no complex multi-step sequence. Any caller who knows a single allowed address can bypass the guard in a single transaction. The allowed addresses are discoverable on-chain via `allowedDepositor` mapping events or direct storage reads.

---

### Recommendation

Replace the `owner` check with a `sender` check in `DepositAllowlistExtension.beforeAddLiquidity`:

```solidity
// Before (wrong — checks caller-supplied owner, not the actual caller)
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}

// After (correct — checks the actual transaction initiator)
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

---

### Proof of Concept

Setup: Pool is configured with `DepositAllowlistExtension`. `allowedDepositor[pool][Alice] = true`. Bob is not on the allowlist.

1. Bob calls `pool.addLiquidity(owner = Alice, salt = X, deltas = ..., callbackData = ..., extensionData = ...)`.
2. The pool calls `_beforeAddLiquidity(msg.sender=Bob, owner=Alice, ...)`.
3. `DepositAllowlistExtension.beforeAddLiquidity` receives `sender=Bob` (ignored) and `owner=Alice`.
4. The check evaluates `allowedDepositor[pool][Alice]` → `true` → no revert.
5. `LiquidityLib.addLiquidity` executes with `owner=Alice`, crediting shares to Alice's `(Alice, X)` position.
6. Bob's `addLiquidityCallback` is invoked; Bob transfers tokens into the pool.
7. Bob has successfully added liquidity to a restricted pool without being on the allowlist. The allowlist guard is fully bypassed.

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
