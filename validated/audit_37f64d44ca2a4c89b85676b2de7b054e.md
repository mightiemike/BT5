### Title
`DepositAllowlistExtension` Checks LP Position `owner` Instead of Actual Depositor `sender`, Allowing Unauthorized Deposits — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual depositor who provides tokens) and instead checks the `owner` parameter (the LP position recipient). Any unauthorized address can bypass the deposit guard by calling `addLiquidity` with an authorized `owner`, causing the allowlist to pass while the real depositor is never checked.

---

### Finding Description

In `DepositAllowlistExtension.beforeAddLiquidity`, the first argument — `sender`, the actual caller of `addLiquidity` — is unnamed and ignored. The allowlist check is performed exclusively against `owner`, the address that will receive the LP position shares:

```solidity
// DepositAllowlistExtension.sol lines 32-42
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
``` [1](#0-0) 

The pool's `addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as the LP recipient:

```solidity
// MetricOmmPool.sol line 191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [2](#0-1) 

Tokens are pulled from `msg.sender` (the actual depositor) via the callback mechanism, while LP shares are minted to `owner`. The allowlist check on `owner` is therefore checking the wrong entity — the recipient of value, not the provider of value.

This is structurally opposite to the correct pattern used by `SwapAllowlistExtension`, which names and checks `sender` while ignoring the second parameter:

```solidity
// SwapAllowlistExtension.sol lines 31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
``` [3](#0-2) 

---

### Impact Explanation

An unauthorized address `A` (not on the allowlist) calls `pool.addLiquidity(owner = B, ...)` where `B` is any address that IS on the allowlist. The extension checks `allowedDepositor[pool][B]` → passes. Tokens are pulled from `A` via callback; LP shares are minted to `B`. If `B` is a contract controlled by `A` (or cooperates with `A`), `B` calls `removeLiquidity` and returns the tokens. `A` has fully interacted with a restricted pool — depositing and withdrawing — while never appearing in the allowlist check. The intended access control (KYC/AML gate, private pool restriction, deposit cap per authorized address) is completely bypassed. This is a broken core pool guard with fund-routing consequences: unauthorized capital enters the pool and LP accounting is affected by a party the pool admin explicitly excluded.

---

### Likelihood Explanation

The bypass requires only that the attacker know any single authorized address (trivially discoverable on-chain via `AllowedToDepositSet` events or `allowedDepositor` reads) and that they control or collude with that address. No privileged role, no special token, no oracle manipulation is needed. Any caller can supply an arbitrary `owner` to `addLiquidity`; the pool imposes no restriction on `sender != owner`.

---

### Recommendation

Check `sender` (the actual depositor) instead of `owner` (the LP recipient), mirroring the correct pattern in `SwapAllowlistExtension`:

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

---

### Proof of Concept

1. Pool `P` is deployed with `DepositAllowlistExtension`. Pool admin calls `setAllowedToDeposit(P, B, true)`. Address `A` is NOT authorized.
2. `A` deploys or controls contract `B` (or simply knows `B` will cooperate).
3. `A` calls `P.addLiquidity(owner = B, salt = 0, deltas = ..., callbackData = ..., extensionData = "")`.
4. Pool calls `_beforeAddLiquidity(sender = A, owner = B, ...)`.
5. `DepositAllowlistExtension.beforeAddLiquidity` evaluates `allowedDepositor[P][B]` → `true` → no revert.
6. `LiquidityLib.addLiquidity` executes; tokens are pulled from `A` via callback; LP shares are credited to `B`.
7. `B` calls `P.removeLiquidity(owner = B, ...)` (enforced: `msg.sender == owner`), receiving the tokens back.
8. Net result: `A` deposited into and withdrew from a pool it was explicitly barred from, with zero on-chain evidence linking `A` to the deposit in the allowlist check. [1](#0-0) [4](#0-3) [5](#0-4)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-40)
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
```
