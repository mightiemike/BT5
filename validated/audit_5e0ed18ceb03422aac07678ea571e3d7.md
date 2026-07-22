### Title
`DepositAllowlistExtension` checks caller-controlled `owner` instead of `sender`, allowing any address to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` argument (the actual depositor) and instead validates the `owner` argument (the LP-position recipient). Because `owner` is a free caller-controlled parameter of `MetricOmmPool.addLiquidity`, any address that is not on the allowlist can bypass the guard by naming an allowlisted address as `owner`.

---

### Finding Description

`DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**. Its hook signature receives `sender` as the first argument and `owner` as the second, but the implementation discards `sender` and checks only `owner`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [1](#0-0) 

The pool passes `msg.sender` as `sender` and the caller-supplied `owner` as the second argument:

```solidity
// metric-core/contracts/MetricOmmPool.sol
function addLiquidity(
    address owner,          // ← fully caller-controlled
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external nonReentrant(PoolActions.ADD_LIQUIDITY) ... {
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    ...
}
``` [2](#0-1) 

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both values, so the extension receives the real depositor in position 0 and the LP-position recipient in position 1:

```solidity
// metric-core/contracts/ExtensionCalling.sol
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [3](#0-2) 

Because the extension ignores `sender` and only tests `owner`, an unprivileged caller can pass any allowlisted address as `owner` and the guard will approve the call. The caller then satisfies the token-transfer callback (paying the tokens), while the LP shares are credited to the named `owner`. The actual depositor — the address providing the capital — is never checked.

The `removeLiquidity` path enforces `msg.sender == owner`, so the attacker cannot reclaim the LP shares; the tokens are permanently deposited into the pool under the allowlisted address's position. The allowlist is therefore completely ineffective at controlling who injects capital into the pool.

---

### Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for restricting who may supply liquidity. With this bug the guard is reduced to checking the LP-position label rather than the depositing party. Any address — including sanctioned, non-KYC'd, or otherwise excluded addresses — can deposit tokens into the pool by naming any allowlisted address as `owner`. This is a direct admin-boundary break: an unprivileged path bypasses a pool-admin-configured access control, and the pool's liquidity composition is no longer under the admin's control.

---

### Likelihood Explanation

Exploitation requires only a standard `addLiquidity` call with a publicly observable allowlisted address as `owner`. No special role, flash loan, or oracle manipulation is needed. Any actor who can read the allowlist mapping (public storage) and call the pool can trigger this immediately.

---

### Recommendation

Replace the ignored first argument with a named `sender` and validate it instead of `owner`:

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

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension`; only `alice` is allowlisted (`allowedDepositor[pool][alice] = true`).
2. `bob` (not allowlisted) calls:
   ```solidity
   pool.addLiquidity(alice, salt, deltas, callbackData, extensionData);
   ```
3. The pool calls `_beforeAddLiquidity(bob, alice, ...)`.
4. The extension evaluates `allowedDepositor[pool][alice]` → `true`; no revert.
5. `LiquidityLib.addLiquidity` credits LP shares to `alice`; the token-transfer callback is executed on `bob`, draining `bob`'s tokens into the pool.
6. `bob` has deposited capital into the allowlisted pool without ever appearing on the allowlist. The allowlist check is fully bypassed.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
```
