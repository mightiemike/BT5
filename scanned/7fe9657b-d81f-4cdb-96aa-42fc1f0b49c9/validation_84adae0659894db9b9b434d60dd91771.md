### Title
`DepositAllowlistExtension` checks LP position `owner` instead of token depositor `sender`, allowing full allowlist bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently ignores the `sender` argument (the address that actually calls `addLiquidity` and provides tokens via callback) and instead gates on the `owner` argument (the LP position recipient, a free caller-supplied parameter). Any unprivileged address can bypass the deposit allowlist entirely by passing an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses into the extension hook:

```solidity
// MetricOmmPool.sol – addLiquidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`msg.sender` is the actual token depositor (the address that will be called back to transfer tokens). `owner` is a free parameter supplied by the caller that determines who receives the LP position.

`ExtensionCalling._beforeAddLiquidity` forwards both:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
```

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first argument but discards it (unnamed `address`), then checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

Because `owner` is caller-controlled, any address can pass the guard by supplying an allowlisted address as `owner`. The naming of the contract (`DepositAllowlistExtension`), its setter (`setAllowedToDeposit`), and its view function (`isAllowedToDeposit(pool, depositor)`) all confirm the intent is to restrict the token depositor, not the position recipient. The analogous `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the swapper), not `recipient`.

---

### Impact Explanation

The deposit allowlist is completely bypassed. Any unprivileged address can provide liquidity to a pool that the admin intended to restrict. This breaks the admin-configured access control boundary: the pool admin's `setAllowedToDeposit` configuration has no effect on who actually deposits tokens. Pools relying on this extension for permissioned liquidity (e.g., private pools, KYC-gated pools, curated market-maker pools) silently accept deposits from any address.

---

### Likelihood Explanation

Exploitation requires no special privileges, no flash loans, and no complex setup. Any address can call `pool.addLiquidity(allowlistedAddress, salt, deltas, callbackData, extensionData)` and the guard passes unconditionally. The bypass is deterministic and repeatable.

---

### Recommendation

Check `sender` (the actual token depositor / `msg.sender` of the pool call) instead of `owner`:

```diff
- function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
+ function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
      external view override returns (bytes4)
  {
-     if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
+     if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
          revert IMetricOmmPoolActions.NotAllowedToDeposit();
      }
      return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` and calls `setAllowedToDeposit(pool, alice, true)`. Bob is not allowlisted.
2. Bob calls `pool.addLiquidity(alice, salt, deltas, callbackData, "")`.
3. The pool calls `extension.beforeAddLiquidity(bob, alice, salt, deltas, "")`.
4. The guard evaluates `allowedDepositor[pool][alice] == true` → passes.
5. Bob's callback transfers Bob's tokens into the pool; the LP position is minted to Alice.
6. Bob has deposited into a pool he was explicitly barred from. The allowlist is fully bypassed. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
