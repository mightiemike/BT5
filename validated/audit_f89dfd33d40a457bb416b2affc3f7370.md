### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Enabling Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which is the direct caller of `MetricOmmPool.swap`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` resolves to the router's address rather than the actual end user. A pool admin who allowlists the router (the natural step to let their curated users trade via the router) inadvertently grants every user—including non-allowlisted ones—the ability to bypass the per-user swap allowlist.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the entity calling the extension), and `sender` is the first argument forwarded by the pool — which is the direct `msg.sender` of `pool.swap(...)`. When `MetricOmmSimpleRouter` executes a swap on behalf of a user, it calls `pool.swap(...)` directly, making the router the `msg.sender` to the pool and therefore the `sender` value the extension receives. [2](#0-1) 

The extension has no way to distinguish between different end users routing through the same router contract. If the pool admin allowlists the router address so that their curated users can trade via the router, the check becomes `allowedSwapper[pool][router] == true`, which passes for every user who routes through `MetricOmmSimpleRouter`, regardless of whether that user is individually allowlisted.

This is structurally inconsistent with `DepositAllowlistExtension`, which correctly ignores `sender` and instead checks `owner` (the LP position owner — the actual economic actor):

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    ...
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
``` [3](#0-2) 

The deposit extension correctly gates the owner regardless of the intermediary; the swap extension does not.

The existing test suite confirms that the allowlist is keyed to the **direct pool caller**, not the end user:

```solidity
swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);
// callers[0] is the TestCaller contract that wraps users[0]
_swap(0, users[0], false, int128(1000), type(uint128).max);
``` [4](#0-3) 

---

### Impact Explanation

Any pool that deploys `SwapAllowlistExtension` to restrict trading to a curated set of addresses and also allowlists `MetricOmmSimpleRouter` (to let those curated users trade via the supported periphery path) will have its allowlist completely bypassed. Any non-allowlisted user can call `MetricOmmSimpleRouter.exactInput` / `exactOutput` targeting the pool and the extension will approve the swap because it sees the router's address, which is allowlisted. This breaks the core access-control invariant of the extension and constitutes a direct policy bypass on curated pools.

---

### Likelihood Explanation

Medium-High. The `MetricOmmSimpleRouter` is the primary supported swap entrypoint in the periphery. Any pool operator who wants their allowlisted users to be able to use the router (the natural UX path) must allowlist the router address, at which point the bypass is immediately active for all users. The operator has no in-protocol mechanism to allowlist the router while still enforcing per-user identity.

---

### Recommendation

The swap allowlist must gate the actual end user, not the intermediary. Two viable approaches:

1. **Router forwards user identity**: `MetricOmmSimpleRouter` passes the actual `msg.sender` (the end user) as the `sender` argument to `pool.swap(...)`, and the pool forwards it to the extension. This mirrors how `DepositAllowlistExtension` receives the true `owner` even through the liquidity adder.

2. **Extension reads transient initiator**: Use EIP-1153 transient storage (already used elsewhere in the protocol for reentrancy guards) to record the original transaction initiator at the router entry point and read it inside the extension, bypassing the intermediary-caller problem entirely.

---

### Proof of Concept

1. Pool is deployed with `SwapAllowlistExtension` wired into `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only `alice` should be able to swap.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — necessary so `alice` can use `MetricOmmSimpleRouter`.
4. `bob` (not individually allowlisted) calls `MetricOmmSimpleRouter.exactInput(...)` targeting the pool.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. The pool calls `_beforeSwap(router, ...)` → extension receives `sender = router`.
7. Extension evaluates `allowedSwapper[pool][router] == true` → passes.
8. `bob`'s swap executes successfully, bypassing the per-user allowlist. [5](#0-4) [6](#0-5)

### Citations

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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
