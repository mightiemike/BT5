### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Unprivileged Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender` of `addLiquidity`, i.e., the party paying tokens) and instead gates on `owner` (the LP position holder). Because `owner` is a free caller-supplied parameter with no other on-chain constraint, any address not on the allowlist can bypass the guard by naming an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses into the extension hook:

```solidity
// MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`msg.sender` is the actual depositor (pays tokens, initiates the call). `owner` is the LP position holder (receives shares), and is a free parameter supplied by the caller with no restriction in the pool itself.

`DepositAllowlistExtension.beforeAddLiquidity` receives both but checks only `owner`:

```solidity
// DepositAllowlistExtension.sol L32-42
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

The first parameter (`sender`) is unnamed and entirely ignored. The allowlist lookup is `allowedDepositor[pool][owner]`, not `allowedDepositor[pool][sender]`.

Compare with `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender`:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

The asymmetry is the root cause: the swap guard checks the actual caller; the deposit guard checks the wrong actor.

---

### Impact Explanation

The pool admin deploys `DepositAllowlistExtension` to restrict which addresses may deposit into the pool. Because the guard checks `owner` rather than `sender`, the restriction is entirely ineffective: any unprivileged address can deposit by supplying an allowlisted address as `owner`. The LP shares are credited to that allowlisted address, and with that address's cooperation (or via a pre-arranged agreement), the actual depositor recovers the underlying tokens. The pool admin's access-control boundary is broken: an unprivileged path bypasses the configured guard, allowing unauthorized LP exposure and fee accrual in a pool intended to be restricted.

---

### Likelihood Explanation

Exploitation requires no special privilege. Any caller of `addLiquidity` can supply an arbitrary `owner`. The only prerequisite is knowing one allowlisted address (publicly observable via `allowedDepositor` or `AllowedToDepositSet` events). The bypass is therefore trivially reachable by any external actor.

---

### Recommendation

Change `DepositAllowlistExtension.beforeAddLiquidity` to check `sender` (the actual depositor) instead of `owner`:

```solidity
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

This mirrors the correct pattern already used in `SwapAllowlistExtension.beforeSwap`.

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` configured. Only Alice (`0xAlice`) is added to the allowlist via `setAllowedToDeposit(pool, Alice, true)`.
2. Bob (`0xBob`, not allowlisted) calls `pool.addLiquidity(owner=Alice, salt=0, deltas=..., ...)`.
3. The pool calls `extension.beforeAddLiquidity(sender=Bob, owner=Alice, ...)`.
4. The extension evaluates `allowedDepositor[pool][Alice]` → `true` → no revert.
5. Bob's tokens are transferred into the pool; LP shares are minted to Alice.
6. Alice calls `pool.removeLiquidity(owner=Alice, ...)`, receives the underlying tokens, and returns them to Bob out-of-band.
7. Net result: Bob has effectively deposited into a pool the admin intended to restrict to Alice only. The deposit allowlist is fully bypassed. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L188-196)
```text
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
