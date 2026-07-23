### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Address to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

The `DepositAllowlistExtension` is intended to gate which addresses may add liquidity to a pool. However, its `beforeAddLiquidity` hook silently discards the `sender` argument (the actual caller and token payer) and instead validates the `owner` argument (the position beneficiary). Any unprivileged address can bypass the allowlist by calling `pool.addLiquidity(owner = allowlisted_address, ...)`, causing the extension to approve the call based on the allowlisted owner's status while the unauthorized caller pays the tokens and injects liquidity.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses into the extension hook:

- `sender` = `msg.sender` — the actual caller who will pay tokens via the swap callback
- `owner` — the position beneficiary, supplied as a parameter by the caller [1](#0-0) 

These are forwarded faithfully through `ExtensionCalling._beforeAddLiquidity`: [2](#0-1) 

Inside `DepositAllowlistExtension.beforeAddLiquidity`, the first parameter (`sender`) is unnamed and silently dropped. The allowlist check is performed only on `owner`: [3](#0-2) 

Because `owner` is a free parameter chosen by the caller, any address can pass an allowlisted address as `owner` and the guard will approve the call, even though the actual depositor (`sender`) is not on the allowlist.

By contrast, `SwapAllowlistExtension.beforeSwap` correctly validates `sender` (the actual swapper): [4](#0-3) 

The asymmetry confirms that checking `sender` is the intended pattern for access control in this hook system.

---

### Impact Explanation

The deposit allowlist guard is completely ineffective. Any unprivileged address can inject liquidity into a pool that the admin intended to restrict, by nominating any allowlisted address as `owner`. The unauthorized depositor pays the tokens (via the callback), the allowlisted address receives the position, and the pool receives liquidity from an actor the admin explicitly excluded. This defeats the purpose of the allowlist (e.g., regulatory gating, curated LP sets, or manipulation prevention) and constitutes an admin-boundary break via an unprivileged path.

---

### Likelihood Explanation

The bypass requires only a single direct call to `pool.addLiquidity` with `owner` set to any known allowlisted address. No special privileges, flash loans, or oracle manipulation are needed. Any address that can observe the allowlist state (which is public via `allowedDepositor`) can execute this immediately.

---

### Recommendation

Change `beforeAddLiquidity` to validate `sender` (the actual depositor/payer) instead of `owner`:

```solidity
// Before (broken):
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}

// After (fixed):
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

This mirrors the correct pattern already used in `SwapAllowlistExtension`.

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` configured. Admin calls `setAllowedToDeposit(pool, alice, true)`. Bob is not allowlisted.
2. Bob calls `pool.addLiquidity(owner = alice, salt, deltas, callbackData, extensionData)`.
3. The pool calls `_beforeAddLiquidity(sender=Bob, owner=alice, ...)`.
4. `DepositAllowlistExtension.beforeAddLiquidity` checks `allowedDepositor[pool][alice]` → `true` → no revert.
5. `LiquidityLib.addLiquidity` executes, computes token amounts, and calls back to Bob (as `msg.sender`) to pay the tokens.
6. Bob pays the tokens; Alice receives the position. The pool has accepted liquidity from an address the admin explicitly excluded.
7. Alice can call `removeLiquidity` to withdraw the tokens at any time. [3](#0-2) [5](#0-4)

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
