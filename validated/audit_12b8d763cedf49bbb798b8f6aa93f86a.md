### Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is designed to gate `addLiquidity` by depositor address. Its `beforeAddLiquidity` hook silently discards the `sender` argument (the actual caller) and instead validates the caller-controlled `owner` parameter. Because `owner` is freely chosen by whoever calls `MetricOmmPool.addLiquidity`, any non-allowlisted address can bypass the guard by supplying an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses into the `beforeAddLiquidity` hook: [1](#0-0) 

```
_beforeAddLiquidity(msg.sender /*sender*/, owner /*owner*/, salt, deltas, extensionData);
```

`ExtensionCalling` forwards both to the extension: [2](#0-1) 

Inside `DepositAllowlistExtension`, the first positional argument (`sender`) is unnamed and therefore ignored. Only `owner` is tested: [3](#0-2) 

```solidity
function beforeAddLiquidity(address /*sender – ignored*/, address owner, …)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

`owner` is a free parameter supplied by the caller of `addLiquidity`. Any non-allowlisted address can pass an allowlisted address as `owner` and the guard will succeed. The analogous `SwapAllowlistExtension` correctly checks `sender` (the actual caller), confirming the asymmetry is unintentional: [4](#0-3) 

```solidity
function beforeSwap(address sender, address /*recipient – ignored*/, …)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    …
}
```

---

### Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for controlling who may add liquidity to a permissioned pool. With this bug the guard is structurally inert: any address — regardless of allowlist status — can call `addLiquidity` and pass the check by nominating any allowlisted address as `owner`. The resulting LP position is credited to that allowlisted address, not the caller, so the attacker forfeits the deposited tokens. However:

- The allowlist invariant is broken: the pool admin cannot enforce deposit restrictions.
- An attacker can force arbitrary liquidity into the pool, altering bin balances and affecting swap execution for all users.
- This fits the "Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" allowed impact category.

---

### Likelihood Explanation

The bypass requires only knowledge of one allowlisted address (publicly readable from `allowedDepositor`) and the willingness to sacrifice the deposited tokens. No privileged access, flash loan, or complex setup is needed. Any external actor can trigger it against any pool that uses `DepositAllowlistExtension`.

---

### Recommendation

Replace the `owner` check with a `sender` check, mirroring `SwapAllowlistExtension`:

```solidity
// DepositAllowlistExtension.sol
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

Also update `setAllowedToDeposit` / `isAllowedToDeposit` documentation to clarify that the allowlisted entity is the transaction initiator, not the position owner.

---

### Proof of Concept

```solidity
// Pool is deployed with DepositAllowlistExtension.
// Admin allowlists only `alice`.
extension.setAllowedToDeposit(pool, alice, true);

// Bob (not allowlisted) calls addLiquidity with owner = alice.
// The hook checks allowedDepositor[pool][alice] == true → passes.
// Bob's tokens enter the pool; the position is credited to alice.
// Bob has bypassed the deposit allowlist entirely.
pool.addLiquidity(
    alice,          // owner  ← allowlisted address chosen by Bob
    0,              // salt
    deltas,
    callbackData,
    extensionData
);
``` [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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
