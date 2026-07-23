### Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing any caller to bypass the deposit allowlist — (`File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` gates deposits by checking the `owner` (position owner) parameter rather than the `sender` (actual caller/operator) parameter. Because `MetricOmmPool.addLiquidity` explicitly supports an operator pattern where `msg.sender != owner`, any unprivileged address can bypass the allowlist by supplying an allowlisted address as `owner` while acting as the real depositor.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes both the actual caller and the position owner to the extension hook:

```solidity
// MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

The pool's own NatDoc confirms the operator pattern is intentional:
> `msg.sender` pays but need not equal `owner` (operator pattern).

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first argument but silently discards it (unnamed `address`), then checks only `owner`:

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
```

`msg.sender` here is the pool (the extension caller), so the check resolves to `allowedDepositor[pool][owner]`. The actual depositing address (`sender`, i.e., the original `msg.sender` to the pool) is never validated.

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender`:

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

The structural inconsistency between the two sibling extensions confirms the deposit extension checks the wrong address.

---

### Impact Explanation

A pool admin who deploys a permissioned pool with `DepositAllowlistExtension` intends to restrict which addresses may deposit. That restriction is fully circumvented:

- Any address not on the allowlist can call `pool.addLiquidity(allowlistedAddress, salt, deltas, callbackData, extensionData)` directly.
- The extension sees `owner = allowlistedAddress` → check passes.
- The pool issues a `metricOmmModifyLiquidityCallback` to the actual caller, who pays the tokens.
- LP shares are credited to `allowlistedAddress`.

If the attacker controls both an allowlisted address and an unlisted address, they can deposit from the unlisted address and withdraw from the allowlisted address, fully bypassing the pool admin's access control. The pool admin's configured security boundary is broken by an unprivileged path.

---

### Likelihood Explanation

- The operator pattern (`msg.sender != owner`) is explicitly documented and supported by `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, owner, ...)`.
- No additional privilege is required; any EOA or contract can call `pool.addLiquidity` directly.
- The bypass requires only that the attacker know one allowlisted address (publicly readable from `allowedDepositor` mapping).
- Likelihood is **High** given the trivial trigger and zero privilege requirement.

---

### Recommendation

Change `DepositAllowlistExtension.beforeAddLiquidity` to check `sender` (the actual caller/operator) instead of `owner`, mirroring the pattern used in `SwapAllowlistExtension`:

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

If the intent is to gate by position owner (not caller), the NatDoc and admin-facing setter must be updated to make this explicit, and the `SwapAllowlistExtension` pattern should be documented as intentionally different.

---

### Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][alice] = true
  bob is NOT on the allowlist

Attack:
  bob calls pool.addLiquidity(
      owner    = alice,   // allowlisted address bob does not own
      salt     = 1,
      deltas   = <valid bins>,
      callbackData = <bob pays tokens in callback>,
      extensionData = ""
  )

Extension check:
  allowedDepositor[pool][alice] == true  →  passes (bob's address never checked)

Result:
  bob pays tokens, alice receives LP shares
  bob controls alice → bob withdraws via alice.removeLiquidity(alice, 1, ...)
  Deposit allowlist fully bypassed with zero privilege
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L146-148)
```text
  /// @notice Mint shares across bins for `(owner, salt)`; pulls tokens via `IMetricOmmModifyLiquidityCallback` on `msg.sender`.
  /// @dev Callback receives native token amounts the pool expects; underpay reverts `InsufficientTokenBalance`. If `DEPOSIT_ALLOWLIST_PROVIDER` is set, `owner` must pass allowlist. `msg.sender` pays but need not equal `owner` (operator pattern).
  /// @param owner Position owner encoded in the pool’s position key.
```
