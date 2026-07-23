### Title
`DepositAllowlistExtension.beforeAddLiquidity` Ignores the `sender` Parameter — Allowlist Guard Checks the Wrong Actor - (File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol)

### Summary
`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` (the actual caller of `addLiquidity`) as its first parameter but leaves it unnamed and never reads it. The allowlist check is performed against `owner` (the position owner) instead. Any unprivileged address can call `addLiquidity` on a restricted pool by supplying an allowlisted address as `owner`, bypassing the caller-level access control entirely.

### Finding Description
`DepositAllowlistExtension.beforeAddLiquidity` has the following signature and body:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [1](#0-0) 

The first `address` parameter — `sender`, the `msg.sender` of the `addLiquidity` call — is unnamed and completely unused. The guard only checks `owner`.

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and an arbitrary caller-supplied address as `owner`:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [2](#0-1) 

Because `owner` is a free parameter chosen by the caller, any address can pass an allowlisted address as `owner` and satisfy the check, regardless of whether the actual caller is on the allowlist.

Compare with `SwapAllowlistExtension.beforeSwap`, which correctly reads and checks `sender`:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [3](#0-2) 

The asymmetry is the defect: `SwapAllowlistExtension` gates the caller; `DepositAllowlistExtension` gates the position owner, leaving the caller entirely unchecked.

### Impact Explanation
A pool configured with `DepositAllowlistExtension` to restrict liquidity provision to a curated set of addresses provides no protection against an arbitrary caller. Any address can call `pool.addLiquidity(allowlistedAddress, salt, deltas, callbackData, extensionData)`, satisfy the allowlist check via the allowlisted `owner`, and inject liquidity into the restricted pool. The caller-level access control — the only mechanism the pool admin has to gate who interacts with the pool — is fully bypassed. This breaks the admin-boundary invariant: an unprivileged path circumvents a factory/pool role check configured by the pool admin.

### Likelihood Explanation
The bypass requires no special privilege: any externally-owned account or contract can call `addLiquidity` with a known allowlisted address as `owner`. Allowlisted addresses are often discoverable on-chain via emitted events (`AllowedToDepositSet`). The attack path is direct and requires no oracle manipulation, flash loans, or multi-step setup.

### Recommendation
Replace the unnamed first parameter with `sender` and check it instead of (or in addition to) `owner`, consistent with `SwapAllowlistExtension`:

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

If the intent is to gate by position owner rather than caller, the parameter should remain as-is but the NatSpec and admin documentation must clearly state that the caller is not gated, so pool admins are not misled about the protection the extension provides.

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension`; only `allowedAddress` is on the allowlist.
2. `allowedDepositor[pool][allowedAddress] = true` (set by pool admin).
3. Attacker (not on allowlist) calls:
   ```solidity
   pool.addLiquidity(
       allowedAddress,   // owner — allowlisted, check passes
       salt,
       deltas,
       callbackData,
       extensionData
   );
   ```
4. Inside `beforeAddLiquidity`, `allowedDepositor[pool][allowedAddress]` is `true` → no revert.
5. Attacker's callback provides tokens; LP shares are credited to `allowedAddress`.
6. The attacker has deposited into a pool they are not authorized to access. The `sender` check that was supposed to block them was never executed because the parameter is unnamed and unused. [1](#0-0) [4](#0-3) [5](#0-4)

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
