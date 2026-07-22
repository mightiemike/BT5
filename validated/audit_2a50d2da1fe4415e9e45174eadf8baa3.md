### Title
`SwapAllowlistExtension` gates the router address instead of the actual swapper, allowing any user to bypass the configured swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router (required for legitimate users to use it), every unprivileged user can bypass the allowlist by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`:

```solidity
// MetricOmmPool.sol:231
_beforeSwap(
    msg.sender,   // ← router address when called via MetricOmmSimpleRouter
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value verbatim as the `sender` argument to every configured extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
``` [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value forwarded above:

```solidity
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` as `msg.sender`:

```solidity
// MetricOmmSimpleRouter.sol:72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

The result is that the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. Two broken outcomes follow:

1. **Bypass**: If the pool admin allowlists the router address (necessary for any legitimate user to use the router), every unprivileged user can bypass the allowlist by routing through `MetricOmmSimpleRouter`.
2. **DoS**: If the pool admin does not allowlist the router, every individually-allowlisted user is blocked from using the router even though they are permitted to swap directly.

The existing unit tests only exercise the extension in isolation (calling `extension.beforeSwap(swapper, ...)` directly with `vm.prank(address(pool))`) and never test the full path through the router, so the mismatch is not caught. [6](#0-5) 

---

### Impact Explanation

A pool admin who deploys a restricted pool (e.g., KYC-gated, institutional-only) and allowlists the router to enable legitimate users to trade through the standard periphery inadvertently opens the pool to all users. Any address can call `MetricOmmSimpleRouter.exactInputSingle()` and the allowlist check passes because the router is the checked identity. The configured access-control boundary is fully bypassed by an unprivileged path with no special permissions required.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the standard, publicly deployed periphery swap contract. Any user who wants to bypass the allowlist simply calls the router instead of the pool directly. No privileged access, no special setup, and no malicious token behavior is required. The pool admin has no on-chain mechanism to distinguish a router call from a direct call at the extension level.

---

### Recommendation

The `SwapAllowlistExtension` should gate on the economically relevant actor. Two options:

1. **Check `sender` against the allowlist but also accept the router as a transparent forwarder**: require the router to pass the original user address in `extensionData`, and have the extension decode and check that address. This requires a protocol-level convention for the router to embed the originating user.

2. **Gate on `sender` only for direct calls; reject router calls unless the router is explicitly trusted**: add a separate `trustedRouter` mapping and, when `sender` is a trusted router, require the actual user address to be passed in `extensionData`.

The simplest safe fix is to not allowlist the router at all and instead require users to call the pool directly when the allowlist is active — but this must be documented clearly, as the current design gives no warning that allowlisting the router opens the gate to everyone.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup: pool with SwapAllowlistExtension configured in BEFORE_SWAP_ORDER.
// Pool admin allowlists `allowedUser` and also allowlists `router`
// so that `allowedUser` can use the router.

function test_swapAllowlist_bypassViaRouter() public {
    // allowedUser is on the allowlist
    swapExtension.setAllowedToSwap(address(pool), allowedUser, true);
    // router is also allowlisted so allowedUser can use it
    swapExtension.setAllowedToSwap(address(pool), address(router), true);

    // seed liquidity
    _addLiquidity(...);

    // bannedUser is NOT on the allowlist
    // Direct swap reverts as expected:
    vm.prank(bannedUser);
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    pool.swap(bannedUser, true, int128(1000), type(uint128).max, "", "");

    // But router swap succeeds — allowlist bypassed:
    vm.prank(bannedUser);
    // bannedUser approves router and calls exactInputSingle
    token0.approve(address(router), type(uint256).max);
    uint256 amountOut = router.exactInputSingle(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            tokenIn: address(token0),
            tokenOut: address(token1),
            zeroForOne: true,
            amountIn: 1000,
            amountOutMinimum: 0,
            recipient: bannedUser,
            deadline: block.timestamp + 1,
            priceLimitX64: 0,
            extensionData: ""
        })
    );
    // swap succeeds — bannedUser bypassed the allowlist
    assertGt(amountOut, 0);
}
```

### Citations

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```

**File:** metric-periphery/test/extensions/SwapAllowlistSubExtension.t.sol (L26-38)
```text
  function test_revertsWhenSwapperNotAllowed() public {
    vm.prank(address(pool));
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    extension.beforeSwap(swapper, address(0), false, 0, 0, 0, 0, 0, "");
  }

  function test_passesWhenSwapperAllowed() public {
    vm.prank(admin);
    extension.setAllowedToSwap(address(pool), swapper, true);

    vm.prank(address(pool));
    extension.beforeSwap(swapper, address(0), false, 0, 0, 0, 0, 0, "");
  }
```
