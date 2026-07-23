Based on my analysis of the codebase, I have found a valid analog.

---

### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Actor to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` is documented as gating `addLiquidity` by **depositor** address. However, it silently discards the `sender` parameter (the actual caller who pays tokens) and instead checks `owner` (a caller-controlled parameter). Any non-allowlisted actor can bypass the guard by passing any allowlisted address as `owner`.

### Finding Description

In `MetricOmmPool.addLiquidity`, the pool calls `_beforeAddLiquidity(msg.sender, owner, ...)` where `msg.sender` is the actual caller (the depositor who will pay tokens via the swap callback) and `owner` is an arbitrary address supplied by that caller. [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both `sender` and `owner` to the extension: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `(address sender, address owner, ...)` but **ignores `sender`** (replaced with an unnamed `address`) and checks only `owner`: [3](#0-2) 

Because `owner` is a free parameter chosen by the caller, any non-allowlisted actor can pass any allowlisted address as `owner` and the guard will pass. The tokens are pulled from the caller (via the `addLiquidity` callback on `msg.sender`), not from `owner`.

Compare with `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender`: [4](#0-3) 

The asymmetry confirms the bug: the deposit extension checks the wrong address.

### Impact Explanation

The `DepositAllowlistExtension` is rendered completely ineffective as an access-control guard. Any unprivileged actor can add liquidity to a restricted pool by setting `owner` to any allowlisted address. The pool admin's configured allowlist is bypassed on every call. This is an admin-boundary break: a factory/pool-admin-configured guard is bypassed by an unprivileged path.

Additionally, the guard misbehaves in the opposite direction: an allowlisted `sender` is blocked if the `owner` they specify is not on the allowlist, preventing legitimate allowlisted depositors from directing their position to a non-allowlisted owner address.

### Likelihood Explanation

Exploitability is trivial and requires no special privileges. Any caller of `pool.addLiquidity` can set `owner` to any allowlisted address (e.g., the pool admin, a known LP, or any address visible on-chain). The bypass works on every pool that has `DepositAllowlistExtension` configured as a `beforeAddLiquidity` hook.

### Recommendation

Replace the unnamed `address` first parameter with `sender` and check `sender` instead of `owner`, mirroring the pattern in `SwapAllowlistExtension`:

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

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` as a `beforeAddLiquidity` hook.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)`. Bob is **not** allowlisted.
3. Bob calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
4. The pool calls `_beforeAddLiquidity(bob, alice, ...)`.
5. The extension checks `allowedDepositor[pool][alice]` → `true` → no revert.
6. Bob's tokens are pulled via the callback; Alice receives the LP position.
7. Bob has successfully deposited into a restricted pool without being on the allowlist. [3](#0-2)

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
