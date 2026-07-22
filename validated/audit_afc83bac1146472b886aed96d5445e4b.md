### Title
`DepositAllowlistExtension` gates on LP position `owner` instead of actual depositor `sender`, allowing any non-allowlisted address to bypass deposit restrictions — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender` of the pool call) and instead checks the caller-supplied `owner` address (the LP position recipient). Because `pool.addLiquidity` accepts `owner` as a free parameter from any caller, a non-allowlisted address can trivially bypass the deposit gate by naming any allowlisted address as `owner`.

### Finding Description

`MetricOmmPool.addLiquidity` accepts `owner` as a caller-supplied argument and passes both the real caller and the supplied owner to the extension:

```solidity
// MetricOmmPool.sol line 191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both values to every configured extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` then **ignores** the first positional argument (`sender`) entirely — it is unnamed — and only checks `owner`:

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

The allowlist admin sets permissions keyed on the depositor address:

```solidity
function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
``` [4](#0-3) 

The contract's own NatSpec states its purpose: *"Gates `addLiquidity` by depositor address, per pool."* The check is applied to the wrong actor.

### Impact Explanation

Any non-allowlisted address `bob` can call:

```
pool.addLiquidity(alice /*allowlisted*/, salt, deltas, callbackData, extensionData)
```

The extension evaluates `allowedDepositor[pool][alice] == true` and passes. `bob` pays the tokens (the callback is dispatched to `msg.sender`) and `alice` receives the LP position. The pool admin's curation policy — restricting which addresses may deposit — is completely bypassed. This breaks the core invariant of the extension: *"A curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it."* Any actor excluded from the allowlist can still inject liquidity into the pool, defeating regulatory, risk, or access-control intent.

### Likelihood Explanation

The bypass requires zero privilege and zero special setup. Any EOA or contract can call `pool.addLiquidity` with an arbitrary `owner`. The only precondition is knowing one allowlisted address, which is readable from public storage (`allowedDepositor`) or from emitted events (`AllowedToDepositSet`). Likelihood is **High**.

### Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual depositor) instead of `owner` (the LP position recipient):

```solidity
function beforeAddLiquidity(address sender, address /*owner*/, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

Also audit `isAllowedToDeposit` and any off-chain tooling that reads `allowedDepositor` to ensure they are updated to reflect the corrected semantics.

### Proof of Concept

1. Deploy a pool with `DepositAllowlistExtension` configured on `beforeAddLiquidity`.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)`. `bob` is **not** allowlisted.
3. `bob` calls `pool.addLiquidity(alice, salt, deltas, callbackData, "")`.
4. Extension evaluates `allowedDepositor[pool][alice] == true` → no revert.
5. `bob`'s tokens are transferred into the pool; `alice` receives the LP shares.
6. `bob` has successfully deposited into a pool he is not authorized to access. [3](#0-2) [5](#0-4)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-20)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
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
