### Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Any Unprivileged Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is the production extension that gates `addLiquidity` by depositor address. Its `beforeAddLiquidity` hook silently drops the `sender` argument and checks only `owner` (the position recipient). Because `owner` is a caller-supplied parameter, any address not on the allowlist can bypass the guard by supplying an allowlisted address as `owner`, while the actual initiator and payer of the deposit goes unchecked.

---

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` receives two address arguments: the first (unnamed, discarded) is `sender` — the direct caller of `pool.addLiquidity()` — and the second is `owner` — the position recipient chosen by the caller.

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol  L32-L42
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

The pool passes `msg.sender` as `sender` and the caller-supplied `owner` parameter as `owner`:

```solidity
// metric-core/contracts/MetricOmmPool.sol  L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`owner` is a free parameter — any caller can set it to any address. Because the extension only checks `allowedDepositor[pool][owner]`, a non-allowlisted actor simply passes an allowlisted address as `owner` and the guard passes unconditionally.

The sibling `SwapAllowlistExtension` correctly checks `sender` (the actual caller):

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol  L31-L40
function beforeSwap(address sender, address, ...)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

The asymmetry confirms the deposit extension has the wrong field.

---

### Impact Explanation

The deposit allowlist is the only on-chain mechanism a pool admin has to restrict who may add liquidity. With this bug the restriction is entirely ineffective: any address can add liquidity to an allowlist-gated pool by nominating any allowlisted address as `owner`. The pool receives liquidity from unauthorized sources, the admin-configured access boundary is broken, and any regulatory, economic, or security rationale behind the allowlist is voided. This is a direct admin-boundary break reachable by an unprivileged path with no special preconditions.

---

### Likelihood Explanation

Exploitation requires only a standard `addLiquidityExactShares` call through `MetricOmmPoolLiquidityAdder` with `owner` set to any address that appears in `allowedDepositor`. The allowlist of a live pool is readable on-chain. No privileged role, flash loan, or price manipulation is needed. Any actor who wants to add liquidity to a restricted pool can do so immediately.

---

### Recommendation

Replace the `owner` check with a `sender` check, consistent with `SwapAllowlistExtension`:

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

If the intent is to allowlist position owners rather than callers, the NatSpec and the mapping key name (`allowedDepositor`) must be updated to reflect that, and the inconsistency with `SwapAllowlistExtension` must be documented explicitly.

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` as an extension. `allowAllDepositors[pool] = false`.
2. Admin calls `setAllowedToDeposit(pool, alice, true)`. Alice is the only allowlisted depositor.
3. Bob (not allowlisted) calls:
   ```solidity
   liquidityAdder.addLiquidityExactShares(
       pool,
       alice,   // owner — allowlisted
       salt,
       deltas,
       maxAmount0,
       maxAmount1,
       extensionData
   );
   ```
4. `MetricOmmPoolLiquidityAdder` calls `pool.addLiquidity(alice, ...)`.
5. Pool calls `_beforeAddLiquidity(msg.sender=LiquidityAdder, owner=alice, ...)`.
6. Extension evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
7. Bob's tokens are pulled via the callback; Alice receives the LP position.
8. The allowlist has been bypassed: Bob, a non-allowlisted address, has successfully added liquidity to the restricted pool.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
