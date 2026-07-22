### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Non-Allowlisted Depositors to Bypass the Gate — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` argument and gates only on `owner`. Because `MetricOmmPool.addLiquidity` explicitly supports an operator pattern (`msg.sender` ≠ `owner`), any non-allowlisted address can deposit tokens into the pool by naming an allowlisted address as `owner`, bypassing the access gate entirely.

---

### Finding Description

`DepositAllowlistExtension` is documented as "Gates `addLiquidity` by depositor address, per pool." [1](#0-0) 

The hook signature receives both `sender` (the actual caller/payer) and `owner` (the position recipient), but the implementation discards `sender` entirely (unnamed `address,`) and checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
``` [1](#0-0) 

The pool's `addLiquidity` explicitly supports the operator pattern — `msg.sender` (the payer) need not equal `owner` (the position recipient):

```solidity
function addLiquidity(address owner, ...) external {
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [2](#0-1) 

The interface comment confirms `msg.sender` pays but need not equal `owner`:

> "msg.sender pays but need not equal owner (operator pattern)." [3](#0-2) 

Compare with `SwapAllowlistExtension`, which correctly checks `sender` (the actual swapper), not `recipient`:

```solidity
function beforeSwap(address sender, ...) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
``` [4](#0-3) 

The `DepositAllowlistExtension` is inconsistent with this pattern and with its own stated purpose.

---

### Impact Explanation

A non-allowlisted address (`sender`) can call `pool.addLiquidity(allowlisted_owner, salt, deltas, ...)`. The extension checks `allowedDepositor[pool][allowlisted_owner]` — which passes — while `sender` is never validated. The non-allowlisted address pays tokens via the modify-liquidity callback, and the allowlisted `owner` receives the position. The pool admin's deposit gate is rendered ineffective: any address can inject liquidity by naming any allowlisted address as `owner`. The allowlisted owner can then call `removeLiquidity` (which requires `msg.sender == owner`) and withdraw the deposited tokens, making this a viable griefing or fund-extraction vector against the depositor.

---

### Likelihood Explanation

The operator pattern is a first-class, documented feature of the pool. The `MetricOmmPoolLiquidityAdder` periphery contract itself calls `pool.addLiquidity(owner, ...)` where `msg.sender` to the pool is the adder contract, not the user — demonstrating that `sender != owner` is a routine, expected flow. [5](#0-4) 

Any user who knows an allowlisted address can exploit this without any privileged access.

---

### Recommendation

Mirror the pattern used in `SwapAllowlistExtension`: check `sender` (the actual depositor/payer), not `owner`:

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

If the intent is instead to gate by position owner (who holds the LP claim), the contract's NatSpec and name should be updated to reflect that, and the `sender` bypass should be explicitly acknowledged.

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` attached to `beforeAddLiquidity`.
2. Admin allowlists `bob` (`allowedDepositor[pool][bob] = true`). `alice` is **not** allowlisted.
3. `alice` calls `pool.addLiquidity(bob /*owner*/, salt, deltas, callbackData, extensionData)`.
4. Pool calls `extension.beforeAddLiquidity(alice /*sender*/, bob /*owner*/, ...)`.
5. Extension checks `allowedDepositor[pool][bob]` → `true` → no revert.
6. Pool proceeds; `alice`'s callback pays tokens; `bob` receives the LP position.
7. `bob` calls `pool.removeLiquidity(bob, salt, deltas, "")` and withdraws the tokens.
8. `alice` has deposited into a restricted pool without being on the allowlist. [1](#0-0) [6](#0-5)

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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L146-148)
```text
  /// @notice Mint shares across bins for `(owner, salt)`; pulls tokens via `IMetricOmmModifyLiquidityCallback` on `msg.sender`.
  /// @dev Callback receives native token amounts the pool expects; underpay reverts `InsufficientTokenBalance`. If `DEPOSIT_ALLOWLIST_PROVIDER` is set, `owner` must pass allowlist. `msg.sender` pays but need not equal `owner` (operator pattern).
  /// @param owner Position owner encoded in the pool’s position key.
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
