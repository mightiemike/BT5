### Title
`DepositAllowlistExtension` Checks Caller-Supplied `owner` Instead of Actual Depositor `sender`, Allowing Non-Allowlisted Addresses to Bypass the Deposit Guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` validates the caller-supplied `owner` parameter against the allowlist, but the actual token payer is `msg.sender` of `addLiquidity`. Because `owner` is a free argument any caller can set to any address, a non-allowlisted actor can pass an allowlisted address as `owner` and the guard passes, depositing tokens into the pool while the allowlist invariant is broken.

---

### Finding Description

`MetricOmmPool.addLiquidity` explicitly supports an operator pattern: `msg.sender` pays tokens via callback, while `owner` (a separate, caller-supplied argument) receives the position. [1](#0-0) 

The pool calls `_beforeAddLiquidity(msg.sender, owner, ...)`, forwarding both addresses to the extension. [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` (first parameter) and `owner` (second parameter), but silently discards `sender` and only checks `owner`:

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

The first parameter (`sender` = `msg.sender` of `addLiquidity`, the actual payer) is unnamed and unused. The guard only asks: "is `owner` allowlisted?" — not "is the depositor allowlisted?"

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual swapper): [4](#0-3) 

The asymmetry is the root cause. The interface passes both `sender` and `owner` to every liquidity hook precisely so extensions can gate on either: [5](#0-4) 

---

### Impact Explanation

Any address not on the allowlist can call `addLiquidity(allowlistedAddress, salt, deltas, ...)`. The extension checks `allowedDepositor[pool][allowlistedAddress]` → `true`, the guard passes, and the non-allowlisted caller pays tokens via callback. Shares are minted under `(allowlistedAddress, salt)`. The pool admin's deposit restriction — the sole mechanism for permissioned liquidity pools — is fully bypassed by any unprivileged actor. The allowlisted address receives a position it did not initiate; the attacker loses the deposited tokens but has broken the access-control invariant the pool was configured to enforce.

---

### Likelihood Explanation

Exploitability requires only knowing one allowlisted address (observable on-chain from past `addLiquidity` calls or `AllowedToDepositSet` events) and calling `addLiquidity` with that address as `owner`. No special role, flash loan, or oracle manipulation is needed. Any externally-owned account or contract can trigger this in a single transaction.

---

### Recommendation

Gate on `sender` (the actual payer) instead of — or in addition to — `owner`. For a strict depositor allowlist:

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

If the intent is to restrict both the payer and the position owner, check both:

```solidity
if (!allowAllDepositors[msg.sender]
    && (!allowedDepositor[msg.sender][sender] || !allowedDepositor[msg.sender][owner])) {
    revert IMetricOmmPoolActions.NotAllowedToDeposit();
}
```

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` and calls `setAllowedToDeposit(pool, alice, true)`. Bob is not allowlisted.
2. Bob calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
3. Pool calls `extension.beforeAddLiquidity(bob /*sender*/, alice /*owner*/, ...)`.
4. Extension checks `allowedDepositor[pool][alice]` → `true`. Guard passes.
5. Pool invokes `IMetricOmmModifyLiquidityCallback(bob).metricOmmModifyLiquidityCallback(...)`. Bob pays tokens.
6. Shares are minted under position key `(alice, salt)`.
7. Bob has deposited into the pool despite not being allowlisted. Alice holds the position; Bob cannot withdraw it (`removeLiquidity` enforces `msg.sender == owner`). [6](#0-5)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L199-212)
```text
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
  {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    if (msg.sender != owner) revert NotPositionOwner();
    _beforeRemoveLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Removed, amount1Removed) = LiquidityLib.removeLiquidity(
      _liquidityContext(), owner, salt, deltas, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterRemoveLiquidity(msg.sender, owner, salt, deltas, amount0Removed, amount1Removed, extensionData);
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

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L14-20)
```text
  function beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) external returns (bytes4);
```
