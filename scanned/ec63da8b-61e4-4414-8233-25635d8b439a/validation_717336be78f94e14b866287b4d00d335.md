### Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Allowlist Bypass via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter against a per-pool allowlist. Because `sender` is the pool's `msg.sender` (the router when swaps are routed through `MetricOmmSimpleRouter`), the guard checks the router's address rather than the actual end-user's address. A pool admin who allowlists the router to enable router-based swaps inadvertently grants every user access, defeating the per-user curation the extension is designed to enforce.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension is called by the pool), and `sender` is whatever the pool passes as the first argument to `_beforeSwap`. The pool's `ExtensionCalling._beforeSwap` forwards `sender` verbatim:

```solidity
function _beforeSwap(address sender, ...) internal {
    _callExtensionsInOrder(
        BEFORE_SWAP_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
    );
}
``` [2](#0-1) 

`sender` is the pool's `msg.sender` — the direct caller of `pool.swap(...)`. When a user swaps through `MetricOmmSimpleRouter`, the router is `msg.sender` of the pool call, so `sender` is the router address, not the end-user.

The pool admin faces an impossible choice:

| Router allowlisted? | Effect |
|---|---|
| Yes | Every user through the router passes the check — per-user curation is nullified |
| No | No user can swap through the router — router is broken for this pool |

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly ignores `sender` and checks `owner` (the LP position owner, which is the user regardless of who calls the pool):

```solidity
function beforeAddLiquidity(address, address owner, ...) external view override returns (bytes4) {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
``` [3](#0-2) 

The asymmetry confirms the swap extension is checking the wrong actor. The test suite confirms `callers[0]` (the direct pool caller) must be allowlisted, not the end-user:

```solidity
swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);
_swap(0, users[0], false, int128(1000), type(uint128).max);
``` [4](#0-3) 

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict swaps to a known set of users (e.g., KYC-verified addresses, institutional counterparties) is fully bypassed the moment the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter` and swap against the pool as if they were allowlisted. This constitutes a broken core pool functionality / curation failure with direct fund-impact: unauthorized users can drain liquidity from a pool that was configured to serve only specific counterparties.

---

### Likelihood Explanation

The router is the standard, documented entry point for swaps. Any pool that (a) deploys `SwapAllowlistExtension` and (b) needs to support router-based swaps must allowlist the router, triggering the bypass automatically. No special attacker capability is required — a standard `MetricOmmSimpleRouter` call suffices.

---

### Recommendation

Gate on the end-user rather than the intermediary. Two options:

1. **Check `recipient` instead of `sender`** — for swap allowlists, the economically relevant actor is the recipient of the output tokens. Replace `sender` with the `recipient` parameter in the check.

2. **Have the router forward the originating user** — the pool's swap interface could accept an explicit `originator` parameter that the router populates with `msg.sender` (the user), and the extension checks that field.

Option 1 is the minimal fix consistent with the existing interface. Option 2 is more general and mirrors how `DepositAllowlistExtension` correctly uses `owner` rather than `sender`.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `beforeSwap`.
2. Pool admin allowlists only `alice` via `setAllowedToSwap(pool, alice, true)`.
3. Pool admin also allowlists the router via `setAllowedToSwap(pool, router, true)` (required for router-based swaps to work).
4. `bob` (not allowlisted) calls `MetricOmmSimpleRouter.swap(pool, ...)`.
5. The router calls `pool.swap(recipient=bob, ...)`. The pool passes `msg.sender = router` as `sender` to the extension.
6. The extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. `bob` successfully swaps in a pool he was explicitly excluded from.

The extension's `isAllowedToSwap(pool, bob)` returns `false`, confirming the bypass is invisible to the allowlist read API:

```solidity
function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
}
``` [5](#0-4)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L27-29)
```text
  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L69-73)
```text
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
```
