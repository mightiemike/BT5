### Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing any unlisted address to bypass the deposit allowlist guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` argument (the actual caller/payer) and validates only `owner` (the position beneficiary). Because `MetricOmmPool.addLiquidity` explicitly supports the operator pattern — `msg.sender` pays but need not equal `owner` — any address that is **not** on the allowlist can call `addLiquidity` with a listed address as `owner`, pass the guard, and inject liquidity into a curated pool.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct identities to the extension hook:

- `sender` = `msg.sender` of the pool call (the actual caller and token payer, via callback)
- `owner` = the position beneficiary supplied by the caller [1](#0-0) 

The pool documentation explicitly acknowledges this split:

> `msg.sender` pays but need not equal `owner` (operator pattern). [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first argument but discards it (unnamed `address,`). The guard only checks `owner`:

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

The contract's own NatSpec says it "Gates `addLiquidity` by depositor address", where "depositor" is the paying caller — but the implementation gates by the beneficiary address instead. [4](#0-3) 

The same bypass is reachable through `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, owner, ...)`, which stores `msg.sender` as the payer but forwards the caller-supplied `owner` to the pool: [5](#0-4) 

In both paths the extension sees `owner` (listed) and passes, while `sender` (unlisted) is never examined.

---

### Impact Explanation

A pool admin deploys a curated pool with `DepositAllowlistExtension` to restrict LP participation to a known set of addresses. Any address outside that set can:

1. Call `pool.addLiquidity(listedOwner, salt, deltas, ...)` directly, or route through `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, listedOwner, ...)`.
2. The extension checks `allowedDepositor[pool][listedOwner]` → passes.
3. The unlisted address pays tokens via the modify-liquidity callback and mints LP shares credited to `listedOwner`.

Consequences:
- The pool's curation policy is completely defeated; any address can participate as a liquidity provider.
- The listed owner receives LP shares they did not request and cannot prevent.
- The unlisted depositor can manipulate bin composition (e.g., concentrate liquidity in specific bins to skew oracle-derived pricing or affect swap execution for other users).
- Because `removeLiquidity` requires `msg.sender == owner`, the injected shares are permanently locked under the listed owner's key, preventing the listed owner from cleanly exiting their own position without also withdrawing the injected shares. [6](#0-5) 

---

### Likelihood Explanation

- Requires a pool configured with `DepositAllowlistExtension` (a supported production extension).
- The attacker must hold and approve the pool tokens, but pays their own funds — no victim approval needed.
- The attack path is a direct call to the public `addLiquidity` entrypoint or the public `MetricOmmPoolLiquidityAdder`; no privileged role is required.
- The only prerequisite is knowing one listed owner address, which is observable on-chain from past `AllowedToDepositSet` events. [7](#0-6) 

---

### Recommendation

Change `beforeAddLiquidity` to validate `sender` (the actual payer/caller) instead of `owner`:

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

This aligns with the `SwapAllowlistExtension` pattern, which correctly gates on `sender`: [8](#0-7) 

Pool admins who legitimately want to gate by beneficiary (`owner`) rather than caller can be given an explicit opt-in flag, but the default must match the documented intent ("depositor address").

---

### Proof of Concept

```
Setup:
  - Pool deployed with DepositAllowlistExtension as beforeAddLiquidity hook
  - allowedDepositor[pool][alice] = true   (alice is listed)
  - bob is NOT listed

Attack:
  1. bob calls pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)
     - pool calls _beforeAddLiquidity(bob /*sender*/, alice /*owner*/, ...)
     - extension receives (bob, alice, ...) but checks allowedDepositor[pool][alice] → true → passes
     - pool mints shares for (alice, salt)
     - pool calls metricOmmModifyLiquidityCallback on bob
     - bob pays token0/token1 to the pool

  Result:
     - bob (unlisted) successfully deposited into the curated pool
     - alice received LP shares she did not request
     - pool's curation invariant is broken
``` [3](#0-2) [9](#0-8)

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
```

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L147-147)
```text
  /// @dev Callback receives native token amounts the pool expects; underpay reverts `InsufficientTokenBalance`. If `DEPOSIT_ALLOWLIST_PROVIDER` is set, `owner` must pass allowlist. `msg.sender` pays but need not equal `owner` (operator pattern).
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L10-11)
```text
/// @title DepositAllowlistExtension
/// @notice Gates `addLiquidity` by depositor address, per pool.
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

**File:** metric-periphery/contracts/interfaces/extensions/IDepositAllowlistExtension.sol (L7-7)
```text
  event AllowedToDepositSet(address indexed pool, address indexed depositor, bool allowed);
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
