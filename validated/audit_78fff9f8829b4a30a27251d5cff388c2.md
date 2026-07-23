### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual depositing caller) and gates on `owner` (the LP position recipient) instead. Any unprivileged address can bypass the allowlist by supplying an allowlisted address as `owner` while remaining the actual `msg.sender` that transfers tokens into the pool.

### Finding Description

`MetricOmmPool.addLiquidity` calls the extension hook with two distinct actor addresses:

```
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`msg.sender` is the address that will be required to pay tokens (via the modify-liquidity callback); `owner` is the address that will receive the LP position shares.

`DepositAllowlistExtension.beforeAddLiquidity` drops the first argument entirely and checks only `owner`:

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

The contract's own NatSpec says it "Gates `addLiquidity` by **depositor** address", but the depositor (`sender`) is never read. Compare with `SwapAllowlistExtension.beforeSwap`, which correctly reads and checks `sender`:

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

The inconsistency is a direct structural analog to the external report's `startsWith('proof')` bug: the wrong field is tested, so the guard passes for inputs it was never meant to allow.

### Impact Explanation

A pool configured with `DepositAllowlistExtension` to restrict deposits to a curated set of addresses (e.g., KYC'd LPs, institutional partners) provides zero access-control protection. Any address can call:

```
pool.addLiquidity(allowlistedAddress, salt, deltas, callbackData, extensionData)
```

The extension sees `owner = allowlistedAddress` (allowlisted), passes the check, and the pool proceeds. The caller (`msg.sender`) transfers tokens into the pool and the LP shares are minted to `allowlistedAddress`. The allowlist guard — the sole mechanism the pool admin has to restrict participation — is completely inoperative. This is an admin-boundary break: an unprivileged path bypasses a factory-configured access-control guard.

### Likelihood Explanation

Exploitation requires no special privileges, no flash loan, and no complex setup. Any address that knows an allowlisted address (publicly readable via `allowedDepositor`) can execute the bypass in a single transaction. Likelihood is high.

### Recommendation

Bind the check to `sender` (the actual depositing caller), matching the stated intent and the pattern used by `SwapAllowlistExtension`:

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

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` in the `beforeAddLiquidity` order.
2. Admin calls `setAllowedToDeposit(pool, alice, true)` — only `alice` is permitted.
3. `bob` (not allowlisted) calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
4. Extension evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
5. `LiquidityLib.addLiquidity` runs; the modify-liquidity callback pulls tokens from `bob`; LP shares are minted to `alice`.
6. `bob` has successfully deposited into a pool he was explicitly barred from, bypassing the only configured guard.

---

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
