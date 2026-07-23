Now let me look at the pool's `addLiquidity` function and the `MetricOmmPoolLiquidityAdder` to understand how `sender` and `owner` are passed:

### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Address to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` validates the LP-position `owner` parameter instead of the `sender` (the actual caller of `addLiquidity`). Because `owner` is a free caller-supplied argument, any unprivileged address can bypass the allowlist by naming an already-allowlisted address as `owner`, depositing tokens into a permissioned pool without being on the allowlist.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts two distinct address arguments:

- `owner` — the beneficiary of the LP position (caller-supplied, arbitrary)
- `msg.sender` — the actual depositor who pays tokens via the modify-liquidity callback

The pool passes both to the extension hook:

```solidity
// MetricOmmPool.sol line 191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`ExtensionCalling._beforeAddLiquidity` encodes them as `(sender, owner, ...)` and forwards to the extension.

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first (unnamed, ignored) parameter and `owner` as its second, then gates on `owner`:

```solidity
// DepositAllowlistExtension.sol lines 32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The first parameter — the actual depositor — is silently discarded. The check `allowedDepositor[pool][owner]` passes whenever `owner` is an allowlisted address, regardless of who `sender` is.

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender`:

```solidity
// SwapAllowlistExtension.sol lines 31-41
function beforeSwap(address sender, address, ...)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

The asymmetry confirms the deposit extension is checking the wrong field.

---

### Impact Explanation

The deposit allowlist is completely ineffective. Any address — including one that has never been KYC'd or approved — can deposit into a permissioned pool by supplying any allowlisted address as `owner`. The LP position is credited to that allowlisted address, but the actual token payment comes from the unpermissioned caller. The pool admin's configured access boundary is silently bypassed on every such call. This breaks the core "permissioned liquidity" invariant the extension is designed to enforce, and constitutes an admin-boundary break via an unprivileged path.

---

### Likelihood Explanation

Exploitation requires no special privileges, no flash loans, and no oracle manipulation. Any EOA or contract can call `pool.addLiquidity(allowlisted_address, salt, deltas, callbackData, extensionData)` directly, or route through `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, allowlisted_address, ...)` — a path the periphery explicitly supports and tests (`test_exactShares_canAddOnBehalfOfAnotherOwner`). The bypass is one function call.

---

### Recommendation

Replace the `owner` check with a `sender` check, mirroring `SwapAllowlistExtension`:

```diff
- function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
+ function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
      external view override returns (bytes4)
  {
-     if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
+     if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
          revert IMetricOmmPoolActions.NotAllowedToDeposit();
      }
      return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` configured; `allowAllDepositors[pool] = false`.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)` — only `alice` is permitted.
3. `bob` (not on the allowlist) calls:
   ```solidity
   pool.addLiquidity(
       alice,   // owner — allowlisted, passes the check
       salt,
       deltas,
       callbackData,   // bob implements the callback and pays tokens
       extensionData
   );
   ```
4. `beforeAddLiquidity` is called with `sender = bob`, `owner = alice`.
5. The extension checks `allowedDepositor[pool][alice]` → `true` → no revert.
6. `bob`'s tokens are pulled; LP shares are credited to `alice`.
7. `bob` has deposited into a pool he is not permitted to access. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
