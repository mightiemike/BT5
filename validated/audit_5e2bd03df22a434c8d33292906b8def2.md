### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Address to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` enforces the allowlist against the **position owner** parameter rather than the **actual caller** (`sender`). Because `addLiquidity` accepts an arbitrary `owner` address, any unprivileged address can pass the allowlist check by nominating an already-allowlisted address as `owner`, while still executing the deposit and paying the tokens themselves.

---

### Finding Description

`MetricOmmPool.addLiquidity` calls `_beforeAddLiquidity(msg.sender, owner, …)`, forwarding both the real caller (`sender = msg.sender`) and the caller-supplied position owner (`owner`) to every configured extension. [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` encodes both values and dispatches them to extensions in the declared order: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the first argument (`sender`) and gates only on `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [3](#0-2) 

The sibling `SwapAllowlistExtension.beforeSwap` demonstrates the correct pattern — it checks `sender` (the actual caller), not `recipient`: [4](#0-3) 

Because `owner` is a free parameter supplied by the caller, any address can pass the allowlist check by setting `owner` to any address that is already on the allowlist. The pool then mints the position under that allowlisted `owner`, and the callback is invoked on the real caller (`msg.sender` of `addLiquidity`) to pull the tokens.

`removeLiquidity` enforces `msg.sender == owner`: [5](#0-4) 

So the allowlisted `owner` receives a position they did not request and can immediately withdraw the deposited tokens, while the unauthorized depositor loses their principal.

---

### Impact Explanation

**Admin-boundary break / direct loss of user principal.**

1. The pool admin's deposit allowlist is completely bypassed by any unprivileged address.
2. The unauthorized depositor pays real tokens into a position owned by an allowlisted address; that address can call `removeLiquidity` and claim those tokens — a direct, irreversible loss of the depositor's principal.
3. Unauthorized liquidity injections alter the pool's bin balances and cursor state, potentially disrupting the pool's intended liquidity profile and harming existing LPs.

---

### Likelihood Explanation

**High.** No special role, privilege, or precondition is required beyond knowing one allowlisted address (which is readable on-chain from `allowedDepositor`). The attack is a single direct call to `pool.addLiquidity` with `owner` set to any allowlisted address. It works through `MetricOmmPoolLiquidityAdder` as well, since that contract also accepts a caller-supplied `owner`. [6](#0-5) 

---

### Recommendation

Replace the `owner` check with a `sender` check, mirroring `SwapAllowlistExtension`:

```solidity
// Before (wrong — checks position owner, not actual caller)
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}

// After (correct — checks the actual caller)
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [3](#0-2) 

---

### Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][alice] = true
  bob is NOT on the allowlist

Attack:
  bob calls pool.addLiquidity(
      owner    = alice,   // allowlisted address
      salt     = 0,
      deltas   = <valid bins/shares>,
      callbackData = ...,
      extensionData = ""
  )

Extension check:
  allowedDepositor[pool][alice] == true  →  check passes

Pool execution:
  position minted with owner = alice
  metricOmmModifyLiquidityCallback called on bob (msg.sender)
  bob pays token0 and token1 to the pool

Result:
  alice calls removeLiquidity(alice, 0, deltas, "")
  → msg.sender (alice) == owner (alice) → passes
  alice receives bob's deposited tokens
  bob has lost his principal; the allowlist was never enforced against him
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-195)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
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
