### Title
Immutable Extension Order Can Silently Omit a Configured Security Guard, Permanently Bypassing Stop-Loss or Allowlist Enforcement for All Swaps — (`metric-core/contracts/ExtensionCalling.sol`, `metric-periphery/contracts/extensions/`)

---

### Summary

`ExtensionCalling.sol` stores both extension addresses (`EXTENSION_1`–`EXTENSION_7`) and their per-hook call orders (`BEFORE_SWAP_ORDER`, `AFTER_SWAP_ORDER`, etc.) as **immutable** values set at pool construction. If a security-critical extension (e.g., `OracleValueStopLossExtension`, `SwapAllowlistExtension`) is registered in an extension slot but its slot index is not encoded into the relevant hook order, `_callExtensionsInOrder` silently skips it for every invocation of that hook — permanently and irrecoverably, since both values are immutable.

---

### Finding Description

In `ExtensionCalling.sol`, `_callExtensionsInOrder` decodes a packed `uint256` order value in 3-bit groups. Each group is an extension index (1–7); a group of `0` terminates the loop:

```solidity
// metric-core/contracts/ExtensionCalling.sol L75-L86
function _callExtensionsInOrder(uint256 order, bytes memory data) private {
    if (order == 0) return;          // ← entire hook skipped if order == 0
    while (true) {
      uint256 extensionIndex = order & 0x7;
      if (extensionIndex == 0) break; // ← stops at first 0-group
      address extension = _extensionAddress(extensionIndex);
      if (extension == address(0)) revert PanicEmptyExtension();
      CallExtension.callExtension(extension, data);
      order >>= 3;
    }
}
``` [1](#0-0) 

All six hook orders and all seven extension addresses are set as immutables in the constructor and can never be changed:

```solidity
// metric-core/contracts/ExtensionCalling.sol L17-L51
address internal immutable EXTENSION_1;
...
uint256 internal immutable BEFORE_SWAP_ORDER;
uint256 internal immutable AFTER_SWAP_ORDER;
...
constructor(PoolExtensions memory extensions, ExtensionOrders memory extensionOrders) {
    EXTENSION_1 = extensions.extension1;
    ...
    BEFORE_SWAP_ORDER = extensionOrders.beforeSwap;
    AFTER_SWAP_ORDER  = extensionOrders.afterSwap;
}
``` [2](#0-1) 

The pool's `swap()` calls `_beforeSwap` and `_afterSwap`, which both delegate to `_callExtensionsInOrder`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-L240
_beforeSwap(
    msg.sender, recipient, zeroForOne, amountSpecified,
    priceLimitX64, packedSlot0Initial, bidPriceX64, askPriceX64, extensionData
);
``` [3](#0-2) 

The factory's own error comments acknowledge only the **reverse** consistency check — that an order must not reference a zero-address extension slot:

```solidity
/// @dev Should never happen if factory validates extensions config.
error PanicInvalidExtensionIndex();
/// @dev Should never happen if factory validates extensions config.
error PanicEmptyExtension();
``` [4](#0-3) 

There is **no symmetric check** that a non-zero extension address must appear in at least one order. A pool can be deployed with `EXTENSION_1 = address(OracleValueStopLossExtension)` and `BEFORE_SWAP_ORDER = 0`, and the stop-loss guard is silently dead for the pool's entire lifetime.

The structural parallel to the external report is exact:

| INVEscrow (external) | Metric OMM (this repo) |
|---|---|
| `escrowImplementation = INVEscrow` (immutable) | `EXTENSION_1 = StopLossExtension` (immutable) |
| `callOnDepositCallback = false` (immutable) | `BEFORE_SWAP_ORDER = 0` (immutable) |
| `onDeposit()` never called → xINV never minted | `_beforeSwap` guard never called → stop-loss never enforced |
| Requires new pool deployment to fix | Requires new pool deployment to fix |

---

### Impact Explanation

If `OracleValueStopLossExtension` or `PriceVelocityGuardExtension` is the configured guard but its index is absent from `BEFORE_SWAP_ORDER`, every swap executes at whatever price the oracle returns — including stale, inverted, or velocity-breached prices — with no circuit-breaker. LPs suffer direct loss of principal as arbitrageurs drain the pool at bad prices. If `SwapAllowlistExtension` or `DepositAllowlistExtension` is similarly omitted from the relevant order, unauthorized actors can swap or deposit, violating the pool's access-control invariant and potentially draining restricted liquidity.

---

### Likelihood Explanation

The order encoding is a non-obvious packed 3-bit-per-slot `uint256`. A pool deployer who correctly sets extension addresses but encodes the order incorrectly (e.g., leaves `beforeSwap = 0` while intending to include the stop-loss) produces a silently broken pool. Because both values are immutable, the error is undetectable on-chain after deployment and cannot be corrected without migrating all LP funds to a new pool — exactly the scenario described in the external report.

---

### Recommendation

The factory (or deployer) should enforce the symmetric invariant at construction time: **for every non-zero extension address `EXTENSION_k`, at least one of the six order immutables must contain index `k`**. Alternatively, expose a view function that returns which extensions are active per hook so that off-chain tooling and governance can verify consistency before a pool goes live.

---

### Proof of Concept

1. Deploy a pool via `MetricOmmPoolFactory` with:
   - `extensions.extension1 = address(oracleValueStopLossExtension)` (non-zero)
   - `extensionOrders.beforeSwap = 0` (stop-loss index 1 absent)
2. Observe: `EXTENSION_1` is non-zero; `BEFORE_SWAP_ORDER` is `0`.
3. Call `pool.swap(...)` while the oracle price is outside the stop-loss threshold.
4. `_beforeSwap` calls `_callExtensionsInOrder(0, ...)` which returns immediately at `if (order == 0) return`.
5. The stop-loss extension is never invoked; the swap executes at the bad price.
6. LP funds are drained at an oracle-permitted but stop-loss-violating price with no revert. [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/ExtensionCalling.sol (L12-15)
```text
  /// @dev Should never happen if factory validates extensions config.
  error PanicInvalidExtensionIndex();
  /// @dev Should never happen if factory validates extensions config.
  error PanicEmptyExtension();
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

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );

```
