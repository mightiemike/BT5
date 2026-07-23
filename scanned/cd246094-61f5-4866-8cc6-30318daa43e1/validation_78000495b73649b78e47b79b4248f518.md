### Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any address to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is designed to gate `addLiquidity` by the depositor's address. However, its `beforeAddLiquidity` hook silently discards the `sender` parameter (the actual caller who provides tokens via callback) and instead validates the `owner` parameter (the position share recipient). Any unprivileged address can bypass the allowlist by calling `addLiquidity` directly on the pool and naming an already-allowed address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` fires the `beforeAddLiquidity` hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

The first argument is `msg.sender` — the address that will be called back to supply tokens. The second is `owner` — the address that will receive the minted shares.

`DepositAllowlistExtension.beforeAddLiquidity` receives these two addresses but **discards the first** (unnamed `address` parameter) and **gates on the second** (`owner`):

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

The check `allowedDepositor[pool][owner]` passes whenever the named position owner is on the allowlist, regardless of who is actually calling `addLiquidity` and supplying tokens. The actual depositor (`sender`) is never inspected.

Compare with `SwapAllowlistExtension`, which correctly names and checks `sender`:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [3](#0-2) 

The asymmetry confirms the deposit extension has the wrong parameter bound.

---

### Impact Explanation

The `DepositAllowlistExtension` guard is completely ineffective. Any address — including one explicitly excluded from the allowlist — can deposit tokens into a restricted pool by:

1. Calling `pool.addLiquidity(allowedAddress, salt, deltas, callbackData, extensionData)` directly.
2. Implementing `metricOmmModifyLiquidityCallback` to supply the required tokens.
3. Receiving shares minted to `allowedAddress` (or to themselves if they are the allowed address and use a different intermediary contract).

The pool admin's intent to restrict liquidity provision to vetted addresses is entirely defeated. Unauthorized token flows enter the pool, breaking the invariant that only approved depositors can provide liquidity. This qualifies as an admin-boundary break where an unprivileged path bypasses a configured guard.

---

### Likelihood Explanation

Exploitation requires no special privileges, no price manipulation, and no front-running. Any EOA can deploy a minimal contract implementing `metricOmmModifyLiquidityCallback` and call `addLiquidity` directly on the pool. The bypass is deterministic and repeatable. Likelihood is **High**.

---

### Recommendation

Rename the first parameter and gate on `sender` (the actual depositor), mirroring `SwapAllowlistExtension`:

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [2](#0-1) 

If the intent is to gate by position owner (not depositor), the NatSpec and admin-facing documentation must be corrected and the allowlist management functions renamed accordingly. Both interpretations cannot be simultaneously correct with the current code.

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` as a `beforeAddLiquidity` hook.
2. Admin calls `setAllowedToDeposit(pool, alice, true)`. Bob is **not** added.
3. Bob deploys `AttackerRouter` implementing `metricOmmModifyLiquidityCallback` that transfers tokens from Bob to the pool.
4. Bob calls `AttackerRouter.deposit(pool, alice, salt, deltas)`, which internally calls:
   ```solidity
   pool.addLiquidity(alice, salt, deltas, callbackData, extensionData);
   ```
5. Pool fires `_beforeAddLiquidity(address(AttackerRouter), alice, ...)`.
6. Extension evaluates `allowedDepositor[pool][alice]` → **true** → no revert.
7. Pool calls `AttackerRouter.metricOmmModifyLiquidityCallback(need0, need1, data)` → Bob's tokens transferred to pool.
8. Shares minted to `alice`.

Bob has deposited into a pool he is explicitly excluded from. If Bob controls `alice` (e.g., a multisig or a contract he owns), he receives the shares as well, achieving a full allowlist bypass with zero financial loss to himself. [4](#0-3) [5](#0-4)

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
