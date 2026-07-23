### Title
`DepositAllowlistExtension` gates on position `owner` instead of actual depositor `sender`, allowing any caller to bypass the deposit allowlist via `MetricOmmPoolLiquidityAdder` — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` checks the `owner` (position owner) parameter rather than the `sender` (the actual caller/payer of the `addLiquidity` call). Because `MetricOmmPoolLiquidityAdder` allows `owner` and the token payer (`msg.sender`) to be completely different addresses, any unprivileged user can bypass the deposit allowlist by supplying an authorized user's address as `owner`.

---

### Finding Description

The pool's `addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` argument as `owner` into the extension hook: [1](#0-0) 

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the first parameter (`sender`) and gates only on `owner`: [2](#0-1) 

```solidity
function beforeAddLiquidity(address, address owner, ...)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` explicitly accepts a caller-supplied `owner` that is independent of `msg.sender` (the actual token payer): [3](#0-2) 

```solidity
function addLiquidityExactShares(
    address pool,
    address owner,   // ← arbitrary, not validated against msg.sender
    ...
) external payable override returns (...) {
    _validateOwner(owner);   // only checks owner != address(0)
    ...
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, ...);
}
```

The pool itself has no requirement that `msg.sender == owner` for `addLiquidity`: [4](#0-3) 

(contrast with `removeLiquidity` which does enforce `msg.sender == owner`). [5](#0-4) 

The internal `_addLiquidity` flow stores `msg.sender` as the token payer and passes the caller-supplied `owner` to the pool: [6](#0-5) 

---

### Impact Explanation

The deposit allowlist is completely bypassable. Any address not on the allowlist can deposit tokens into a curated pool by calling `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, authorizedUser, ...)`. The extension sees `owner = authorizedUser` (who is on the allowlist) and passes. The unauthorized caller's tokens enter the pool and are credited to `authorizedUser`'s position. The pool admin's intent — to restrict which addresses may supply liquidity — is fully defeated. Unauthorized funds enter the pool's bin reserves, corrupting the curated LP composition the allowlist was designed to enforce.

---

### Likelihood Explanation

Exploitation requires only knowing any one authorized depositor's address, which is trivially discoverable on-chain from past `addLiquidity` transactions or allowlist-set events. No special privilege, flash loan, or oracle manipulation is needed. Any user can execute this in a single transaction through the publicly deployed `MetricOmmPoolLiquidityAdder`.

---

### Recommendation

`DepositAllowlistExtension.beforeAddLiquidity` must gate on `sender` (the actual caller/payer) rather than `owner` (the position beneficiary):

```solidity
// current (wrong actor)
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {

// fix (gate on the actual depositor)
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
```

This mirrors the correct pattern already used in `SwapAllowlistExtension.beforeSwap`, which gates on `sender` (the direct caller of `pool.swap`). [7](#0-6) 

---

### Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  Alice  → NOT on allowlist
  Bob    → IS on allowlist (allowedDepositor[pool][Bob] = true)

Attack:
  Alice calls:
    MetricOmmPoolLiquidityAdder.addLiquidityExactShares(
        pool,
        Bob,    // owner = authorized address
        salt,
        deltas,
        maxAmount0,
        maxAmount1,
        ""
    )

Pool flow:
  pool.addLiquidity(Bob, salt, deltas, ...) called by LiquidityAdder
  → _beforeAddLiquidity(LiquidityAdder, Bob, ...)
  → DepositAllowlistExtension.beforeAddLiquidity(LiquidityAdder, Bob, ...)
      checks allowedDepositor[pool][Bob] → true → PASSES
  → LiquidityLib.addLiquidity credits Bob's position
  → callback pulls tokens from Alice (the payer)

Result:
  Alice's tokens enter the pool despite Alice not being on the allowlist.
  Bob receives shares Alice funded.
  The deposit allowlist is fully bypassed.
```

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

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L183-207)
```text
  function _addLiquidity(
    address pool,
    address positionOwner,
    uint80 salt,
    LiquidityDelta memory deltas,
    address payer,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) internal returns (uint256 amount0Added, uint256 amount1Added) {
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
    ) {
      amount0Added = a0;
      amount1Added = a1;
      _clearPayContext();
    } catch (bytes memory reason) {
      _clearPayContext();
      assembly ("memory-safe") {
        revert(add(reason, 32), mload(reason))
      }
    }
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
