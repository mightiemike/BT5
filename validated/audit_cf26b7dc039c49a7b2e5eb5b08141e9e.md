### Title
`DepositAllowlistExtension` Checks Position `owner` Instead of Actual Caller `sender`, Allowing Any Address to Bypass the Deposit Guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` argument (the real `msg.sender` of the `addLiquidity` call) and instead validates the caller-supplied `owner` (the position recipient). Because `owner` is a free parameter in `MetricOmmPool.addLiquidity`, any address that is not on the allowlist can bypass the guard by nominating a listed address as `owner`.

---

### Finding Description

**Call chain:**

`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` and forwards `msg.sender` as `sender` to the extension layer:

```
MetricOmmPool.addLiquidity(owner, …)
  → _beforeAddLiquidity(msg.sender, owner, …)          // ExtensionCalling.sol L88-98
    → IMetricOmmExtensions.beforeAddLiquidity(sender=msg.sender, owner=owner, …)
```

`DepositAllowlistExtension.beforeAddLiquidity` then silently discards `sender` (the first parameter is unnamed) and checks only `owner`:

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

`owner` is a free, caller-controlled parameter — there is no constraint in the pool that `owner == msg.sender` for `addLiquidity` (only `removeLiquidity` enforces `msg.sender == owner`). Therefore any unlisted address can pass a listed address as `owner` and the guard passes.

**Contrast with `SwapAllowlistExtension`**, which correctly checks `sender` (the actual caller):

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, …) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    …
}
```

The asymmetry is the root cause: the swap guard checks the real actor; the deposit guard does not.

---

### Impact Explanation

The `DepositAllowlistExtension` is the only on-chain mechanism a pool admin has to restrict who may add liquidity. With this bypass, any address — regardless of allowlist status — can:

1. Deposit tokens into a restricted pool, gaining an LP position under a listed address's key.
2. Coordinate with the listed address to subsequently `removeLiquidity` and recover the tokens, effectively laundering the deposit through the allowlist.
3. Inflate pool liquidity in ways the admin did not authorise, affecting fee accrual, bin cursor movement, and stop-loss watermarks.

The allowlist guard is rendered completely ineffective for any caller who is willing to name a listed address as `owner`. This is a broken core pool access-control invariant.

---

### Likelihood Explanation

The bypass requires no special privileges, no flash loan, and no complex setup. Any EOA or contract can call `pool.addLiquidity(listedAddress, salt, deltas, callbackData, extensionData)` directly. The only prerequisite is knowing one listed address (which is observable on-chain via `AllowedToDepositSet` events or `allowedDepositor` reads). Likelihood is **high**.

---

### Recommendation

Change `DepositAllowlistExtension.beforeAddLiquidity` to check `sender` (the actual caller) instead of `owner`, mirroring the pattern used in `SwapAllowlistExtension`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol
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

```
Setup:
  pool  = MetricOmmPool with DepositAllowlistExtension configured
  bob   = allowedDepositor[pool][bob] = true
  alice = allowedDepositor[pool][alice] = false  (not on allowlist)

Attack:
  1. alice calls pool.addLiquidity(bob, salt, deltas, callbackData, extensionData)
     → pool calls _beforeAddLiquidity(sender=alice, owner=bob, …)
     → extension checks allowedDepositor[pool][bob] == true  ✓  (guard passes)
     → pool calls alice.metricOmmModifyLiquidityCallback(amount0, amount1, …)
     → alice pays tokens; position is recorded under (bob, salt)

  2. alice and bob coordinate: bob calls pool.removeLiquidity(bob, salt, deltas, "")
     → tokens returned to bob, who forwards them to alice

Result: alice deposited into a pool she was explicitly barred from, bypassing the
        DepositAllowlistExtension guard entirely.
```

**Relevant code locations:**

- Guard check (wrong parameter): [1](#0-0) 
- Pool passes `msg.sender` as `sender`, `owner` as free param: [2](#0-1) 
- Extension encoding confirms `sender = msg.sender`, `owner = owner`: [3](#0-2) 
- `removeLiquidity` enforces `msg.sender == owner` (deposit does not): [4](#0-3) 
- Correct pattern in `SwapAllowlistExtension` (checks `sender`): [5](#0-4)

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
