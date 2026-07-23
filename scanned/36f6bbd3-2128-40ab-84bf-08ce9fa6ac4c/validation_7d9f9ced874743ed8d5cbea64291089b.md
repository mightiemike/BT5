### Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Gate — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is the production extension that gates `addLiquidity` by "depositor address, per pool." Its `beforeAddLiquidity` hook silently discards the `sender` argument (the actual `msg.sender` of the pool call) and instead validates the `owner` argument (the LP-position owner, a caller-controlled parameter). Because the pool imposes no constraint that `owner == msg.sender`, any non-allowlisted address can bypass the gate by supplying an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner` to the extension hook:

```solidity
// MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`ExtensionCalling._beforeAddLiquidity` forwards both to the extension:

```solidity
// ExtensionCalling.sol L97
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
```

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first argument but discards it (unnamed `address,`) and gates on `owner` instead:

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

The sister extension `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual swapper) and ignores `recipient`:

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

The pool enforces no constraint that `owner == msg.sender` in `addLiquidity`. `MetricOmmPoolLiquidityAdder._validateOwner` only rejects `address(0)`. Therefore, any caller can freely set `owner` to any allowlisted address, satisfying `allowedDepositor[pool][owner]` and passing the gate.

---

### Impact Explanation

A non-allowlisted address calls `pool.addLiquidity(allowlisted_address, salt, deltas, callbackData, extensionData)` directly or through the `MetricOmmPoolLiquidityAdder`. The extension checks `allowedDepositor[pool][allowlisted_address]` → `true` → passes. The caller pays the tokens; the LP position is minted to `allowlisted_address`. The caller cannot reclaim the position (since `removeLiquidity` enforces `msg.sender == owner`), but the pool's token composition and bin state are permanently altered. Existing LPs suffer dilution of their proportional claim on pool assets — a direct reduction in the value of their LP shares. For pools with significant TVL or strict access control requirements (e.g., institutional or compliance-gated pools), this constitutes a broken core invariant: the deposit allowlist fails to restrict who can alter pool state.

---

### Likelihood Explanation

Allowlisted addresses are publicly visible on-chain via `AllowedToDepositSet` events. Any actor who observes these events can immediately identify a valid `owner` to supply. No privileged access, flash loan, or special setup is required — a single direct call to `pool.addLiquidity` suffices. The only cost to the attacker is the token amount deposited, which is permanently locked in the allowlisted address's LP position.

---

### Recommendation

Replace the `owner` check with a `sender` check, mirroring `SwapAllowlistExtension`:

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

This ensures the gate validates the actual caller of `addLiquidity`, not the caller-controlled position-owner parameter.

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` as a configured extension.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)` — only Alice is allowlisted.
3. Bob (not allowlisted) calls `pool.addLiquidity(alice, salt, deltas, callbackData, "")` directly.
4. Pool calls `_beforeAddLiquidity(bob, alice, ...)` → extension receives `sender=bob, owner=alice`.
5. Extension discards `bob`, checks `allowedDepositor[pool][alice]` → `true` → no revert.
6. Bob's tokens are pulled via callback; the LP position is minted to Alice.
7. Bob has successfully deposited into a pool that was supposed to block him. Pool composition and existing LP share values are altered without authorization. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L188-195)
```text
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
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
