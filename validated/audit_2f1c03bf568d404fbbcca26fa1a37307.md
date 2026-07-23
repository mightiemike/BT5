### Title
`beforeRemoveLiquidity` Extension Hook Has No Bypass Path — Configured Guard Can Permanently Lock LP Funds - (File: `metric-core/contracts/MetricOmmPool.sol`, `metric-core/contracts/ExtensionCalling.sol`)

---

### Summary

The `removeLiquidity` function in `MetricOmmPool` unconditionally calls `_beforeRemoveLiquidity`, which dispatches to every extension registered in `BEFORE_REMOVE_LIQUIDITY_ORDER`. If any extension reverts — whether due to oracle staleness, an allowlist denial, or a stop-loss trigger — the entire withdrawal is blocked. There is no alternative path for an LP to reclaim their assets. This is the direct on-chain analog of Linea's centralized-sequencer censorship: a configured guard acting as a single point of failure that can permanently lock user principal.

---

### Finding Description

`MetricOmmPool.removeLiquidity` executes the following sequence unconditionally:

```solidity
// metric-core/contracts/MetricOmmPool.sol  lines 199-212
function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
  external
  nonReentrant(PoolActions.REMOVE_LIQUIDITY)
  returns (uint256 amount0Removed, uint256 amount1Removed)
{
  if (deltas.binIdxs.length == 0) return (0, 0);
  if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
  if (msg.sender != owner) revert NotPositionOwner();
  _beforeRemoveLiquidity(msg.sender, owner, salt, deltas, extensionData);   // ← blocking point
  (amount0Removed, amount1Removed) = LiquidityLib.removeLiquidity(...);
  _afterRemoveLiquidity(...);
}
``` [1](#0-0) 

`_beforeRemoveLiquidity` calls `_callExtensionsInOrder`, which iterates every slot in `BEFORE_REMOVE_LIQUIDITY_ORDER` and calls each registered extension via `CallExtension.callExtension`. A revert from any extension propagates up and aborts the entire transaction:

```solidity
// metric-core/contracts/ExtensionCalling.sol  lines 75-86
function _callExtensionsInOrder(uint256 order, bytes memory data) private {
  if (order == 0) return;
  while (true) {
    uint256 extensionIndex = order & 0x7;
    if (extensionIndex == 0) break;
    address extension = _extensionAddress(extensionIndex);
    if (extension == address(0)) revert PanicEmptyExtension();
    CallExtension.callExtension(extension, data);   // ← uncaught revert kills removeLiquidity
    order >>= 3;
  }
}
``` [2](#0-1) 

All six extension slots (`EXTENSION_1`–`EXTENSION_7`) and all six hook orders (`BEFORE_REMOVE_LIQUIDITY_ORDER`, etc.) are **immutable**, set once at construction and never changeable:

```solidity
// metric-core/contracts/ExtensionCalling.sol  lines 17-51
address internal immutable EXTENSION_1;
...
uint256 internal immutable BEFORE_REMOVE_LIQUIDITY_ORDER;
...
constructor(PoolExtensions memory extensions, ExtensionOrders memory extensionOrders) {
  BEFORE_REMOVE_LIQUIDITY_ORDER = extensionOrders.beforeRemoveLiquidity;
  ...
}
``` [3](#0-2) 

The periphery ships three guard extensions that are legitimate candidates for the `beforeRemoveLiquidity` slot:

- `OracleValueStopLossExtension.sol` — reverts when oracle-derived value crosses a threshold
- `PriceVelocityGuardExtension.sol` — reverts when price moves too fast
- `DepositAllowlistExtension.sol` — reverts when the caller is not allowlisted



Any of these, when placed in `BEFORE_REMOVE_LIQUIDITY_ORDER`, can permanently block every LP's `removeLiquidity` call with no alternative withdrawal path in the protocol.

---

### Impact Explanation

**Severity: High — Direct, permanent loss of LP principal.**

If a `beforeRemoveLiquidity` extension enters a persistent revert state (oracle goes stale/zero/inverted for `OracleValueStopLossExtension`; price velocity stays elevated for `PriceVelocityGuardExtension`; an LP is removed from the allowlist for `DepositAllowlistExtension`), the LP's shares recorded in `_positionBinShares` become permanently unredeemable. The underlying token balances remain in the pool but are inaccessible to the LP. There is no escape hatch, no admin override on the pool itself (only `onlyFactory` functions exist, none of which bypass extensions), and no L1-style forced-withdrawal path. [4](#0-3) 

---

### Likelihood Explanation

**Medium.** The trigger requires a pool to be deployed with a guard extension in the `beforeRemoveLiquidity` slot. This is a supported, documented configuration path (the periphery ships these extensions precisely for this purpose). Once deployed, any of the following real-world events causes the lock:

- Oracle feed goes stale or returns zero/inverted bid≥ask (stop-loss extension fails closed)
- Market volatility keeps price velocity above the guard threshold indefinitely
- Pool admin removes a user from the deposit allowlist (semi-trusted, not fully privileged)

None of these require an attacker; they are normal operational failure modes.

---

### Recommendation

1. **Add a guardian-free withdrawal path.** Introduce a `removeLiquidityEmergency` function (or a flag on `removeLiquidity`) that skips `_beforeRemoveLiquidity` and `_afterRemoveLiquidity` hooks, callable only by the position owner, so LPs can always reclaim their principal regardless of extension state.

2. **Separate remove-liquidity guards from deposit guards.** The allowlist and oracle guard extensions should explicitly distinguish between blocking new deposits (acceptable) and blocking withdrawals (unacceptable). A `canRemoveLiquidity` check should default to `true` unless the extension explicitly opts in.

3. **Fail-open on oracle errors for withdrawal hooks.** If `OracleValueStopLossExtension` or `PriceVelocityGuardExtension` is used in the `beforeRemoveLiquidity` slot, oracle failures should allow the withdrawal to proceed rather than revert.

---

### Proof of Concept

1. Deploy a pool via `MetricOmmPoolFactory` with `OracleValueStopLossExtension` registered in `extensionOrders.beforeRemoveLiquidity`.
2. LP calls `addLiquidity`, depositing 100 USDC and 1 ETH. Shares are recorded in `_positionBinShares`.
3. The oracle feed becomes stale (returns zero or bid ≥ ask). `OracleValueStopLossExtension.beforeRemoveLiquidity` reverts on every call.
4. LP calls `removeLiquidity`. The call reaches `_beforeRemoveLiquidity` → `_callExtensionsInOrder` → `CallExtension.callExtension(extension, data)` → extension reverts → entire transaction reverts.
5. LP has no other function to call. `removeLiquidity` is the only withdrawal path. Funds are permanently locked.
6. Even if the oracle is later fixed, the extension address and order are immutable — the pool cannot be reconfigured. [1](#0-0) [5](#0-4)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L96-99)
```text
  mapping(int256 => uint256) internal _binTotalShares;
  /// @dev Per-bin position shares keyed by `_positionBinKey`.
  mapping(bytes32 => uint256) internal _positionBinShares;

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

**File:** metric-core/contracts/ExtensionCalling.sol (L17-51)
```text
  address internal immutable EXTENSION_1;
  address internal immutable EXTENSION_2;
  address internal immutable EXTENSION_3;
  address internal immutable EXTENSION_4;
  address internal immutable EXTENSION_5;
  address internal immutable EXTENSION_6;
  address internal immutable EXTENSION_7;
  /// @dev Order of extension calls for before add liquidity.
  uint256 internal immutable BEFORE_ADD_LIQUIDITY_ORDER;
  /// @dev Order of extension calls for after add liquidity.
  uint256 internal immutable AFTER_ADD_LIQUIDITY_ORDER;
  /// @dev Order of extension calls for before remove liquidity.
  uint256 internal immutable BEFORE_REMOVE_LIQUIDITY_ORDER;
  /// @dev Order of extension calls for after remove liquidity.
  uint256 internal immutable AFTER_REMOVE_LIQUIDITY_ORDER;
  /// @dev Order of extension calls for before swap.
  uint256 internal immutable BEFORE_SWAP_ORDER;
  /// @dev Order of extension calls for after swap.
  uint256 internal immutable AFTER_SWAP_ORDER;

  constructor(PoolExtensions memory extensions, ExtensionOrders memory extensionOrders) {
    EXTENSION_1 = extensions.extension1;
    EXTENSION_2 = extensions.extension2;
    EXTENSION_3 = extensions.extension3;
    EXTENSION_4 = extensions.extension4;
    EXTENSION_5 = extensions.extension5;
    EXTENSION_6 = extensions.extension6;
    EXTENSION_7 = extensions.extension7;
    BEFORE_ADD_LIQUIDITY_ORDER = extensionOrders.beforeAddLiquidity;
    AFTER_ADD_LIQUIDITY_ORDER = extensionOrders.afterAddLiquidity;
    BEFORE_REMOVE_LIQUIDITY_ORDER = extensionOrders.beforeRemoveLiquidity;
    AFTER_REMOVE_LIQUIDITY_ORDER = extensionOrders.afterRemoveLiquidity;
    BEFORE_SWAP_ORDER = extensionOrders.beforeSwap;
    AFTER_SWAP_ORDER = extensionOrders.afterSwap;
  }
```

**File:** metric-core/contracts/ExtensionCalling.sol (L75-86)
```text
  function _callExtensionsInOrder(uint256 order, bytes memory data) private {
    if (order == 0) return;

    while (true) {
      uint256 extensionIndex = order & 0x7;
      if (extensionIndex == 0) break;
      address extension = _extensionAddress(extensionIndex);
      if (extension == address(0)) revert PanicEmptyExtension();
      CallExtension.callExtension(extension, data);
      order >>= 3;
    }
  }
```

**File:** metric-core/contracts/ExtensionCalling.sol (L118-129)
```text
  function _beforeRemoveLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_REMOVE_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeRemoveLiquidity, (sender, owner, salt, deltas, extensionData))
    );
  }
```
