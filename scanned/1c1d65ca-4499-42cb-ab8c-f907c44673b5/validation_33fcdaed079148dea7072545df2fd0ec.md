### Title
SwapAllowlistExtension Gates on Router Address Instead of End-User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]`, where `sender` is the direct `msg.sender` to the pool. When users swap through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks whether the **router** is allowlisted rather than the **end user**. If the pool admin adds the router to the allowlist (the natural fix for blocked router-mediated swaps), every unprivileged user can bypass the individual allowlist entirely.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the extension is called by the pool). `sender` is whatever the pool passes as the first argument to `_beforeSwap`. The pool sets `sender = msg.sender` — i.e., the direct caller of `pool.swap(...)`.

The `FullMetricExtensionTest` confirms this binding: the test allowlists `address(callers[0])` (the `TestCaller` contract that directly calls the pool), not `users[0]` (the end user):

```solidity
// metric-periphery/test/extensions/FullMetricExtension.t.sol L69-73
swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);
_addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
_swap(0, users[0], false, int128(1000), type(uint128).max);
```

When a user routes through `MetricOmmSimpleRouter`:

```
user → MetricOmmSimpleRouter.exactInputSingle(...)
     → pool.swap(recipient, amount, ...)   // msg.sender = router
     → _beforeSwap(router, ...)            // sender = router
     → allowedSwapper[pool][router]        // checks router, not user
```

This creates two failure modes:

1. **Broken functionality (no admin action needed):** Individually allowlisted users who use the router are blocked because `allowedSwapper[pool][router]` is `false`. The router is not in the allowlist.

2. **Full bypass (one admin action):** The pool admin, observing that router-mediated swaps are broken, adds the router to the allowlist. Now `allowedSwapper[pool][router] = true`, and **every** user — including those the allowlist was designed to exclude — can bypass the gate by routing through the public router.

The `SwapAllowlistExtension` is documented as gating `swap` by swapper address per pool. The invariant it is supposed to enforce — that only allowlisted addresses can trade — is broken for any pool that uses the supported periphery router.

---

### Impact Explanation

**High.** A pool configured with `SwapAllowlistExtension` to restrict trading to KYC'd or otherwise curated addresses loses that protection entirely once the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle(...)` and execute swaps on the restricted pool. This is a direct policy bypass on a production extension whose sole purpose is access control. Pools relying on the allowlist for regulatory compliance or risk management are fully exposed.

---

### Likelihood Explanation

**High.** The bypass requires only that the pool admin has added the router to the allowlist — a natural and expected operational step when users report that router-mediated swaps are failing. The router is a public, permissionless periphery contract callable by any address. No special privileges, flash loans, or multi-step setup are required beyond a single `router.exactInputSingle(...)` call.

---

### Recommendation

The extension must check the **economic actor** (the end user), not the intermediary. Two options:

1. **Pass the original caller through the router.** Have `MetricOmmSimpleRouter` pass `msg.sender` (the end user) as an explicit `sender` argument to `pool.swap(...)`, and have the pool forward it to the extension rather than using `msg.sender` to the pool. This requires a pool-level change to accept a trusted sender from whitelisted routers.

2. **Check `recipient` instead of `sender` in the extension.** If the pool's `recipient` is always the end user (as is typical in router flows), the extension can gate on `recipient`. This is simpler but requires verifying that `recipient` cannot be spoofed.

The core invariant to restore: the identity checked by `SwapAllowlistExtension` must be the same identity that receives the economic benefit of the swap, regardless of which supported entrypoint is used.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Pool admin calls `swapExtension.setAllowedToSwap(pool, alice, true)` — Alice is the only allowlisted user.
3. Pool admin calls `swapExtension.setAllowedToSwap(pool, address(router), true)` — added to fix broken router swaps.
4. Bob (not allowlisted) calls `router.exactInputSingle({tokenIn, tokenOut, pool, recipient: bob, ...})`.
5. Router calls `pool.swap(bob, amount, ...)` — `msg.sender` to pool = router.
6. Pool calls `_beforeSwap(router, bob, ...)` — `sender` = router.
7. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob successfully swaps on a pool he was explicitly excluded from. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-13)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
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
