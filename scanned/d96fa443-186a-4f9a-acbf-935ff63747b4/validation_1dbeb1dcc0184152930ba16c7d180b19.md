### Title
`SwapAllowlistExtension#beforeSwap` checks the router's address as `sender` instead of the original user, allowing any unprivileged caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is designed to restrict which addresses may swap in a pool. Its `beforeSwap` hook receives `sender` from the pool, which is always `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router address, not the original user. If the pool admin allowlists the router (the only way to let allowlisted users swap through it), the allowlist gate is opened to every caller of the public router.

---

### Finding Description

**Invariant broken**: The swap allowlist must gate the economically relevant actor — the original user initiating the trade — not an intermediate public contract.

**Root cause in `MetricOmmPool.swap`**: [1](#0-0) 

The pool passes `msg.sender` (the direct caller) as `sender` to `_beforeSwap`. When `MetricOmmSimpleRouter` calls `pool.swap()`, `msg.sender` is the router, so `sender = router`.

**Root cause in `ExtensionCalling._beforeSwap`**: [2](#0-1) 

The `sender` forwarded to every extension is the direct pool caller, not the originating EOA.

**Root cause in `SwapAllowlistExtension.beforeSwap`**: [3](#0-2) 

The check `allowedSwapper[msg.sender][sender]` evaluates `allowedSwapper[pool][router]` for every router-mediated swap, regardless of who called the router.

**Router call site** — `sender` is always the router for single-hop swaps: [4](#0-3) 

For multi-hop `exactInput`, intermediate hops use `address(this)` (the router) as payer, so `sender` is still the router for every hop: [5](#0-4) 

---

### Impact Explanation

**Scenario — allowlist completely bypassed**:

1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to KYC'd addresses.
2. Admin calls `setAllowedToSwap(pool, alice, true)` for each approved user.
3. Admin also calls `setAllowedToSwap(pool, router, true)` so that approved users can use the public router — the only way to enable router access for them.
4. Bob (not KYC'd, not allowlisted) calls `router.exactInputSingle(...)`.
5. The router calls `pool.swap(...)`. The pool passes `sender = router` to the extension.
6. The extension evaluates `allowedSwapper[pool][router] == true` → passes.
7. Bob's swap executes in a pool that was supposed to be restricted to approved users only.

The allowlist provides zero protection once the router is allowlisted. Any user with token approval can swap in the restricted pool. This is a direct, fund-impacting bypass: the pool's LP assets are exposed to unrestricted trading, defeating the purpose of the allowlist (e.g., regulatory compliance, curated LP pools, or protocol-controlled liquidity).

**Alternate scenario — allowlisted users locked out**:

If the admin does not allowlist the router, approved users cannot use the router at all and must call `pool.swap()` directly, breaking the intended UX and making the periphery unusable for allowlisted pools.

---

### Likelihood Explanation

- The router is a public, permissionless contract — any address can call it.
- The admin *must* allowlist the router to give approved users router access; there is no other mechanism.
- No special setup, flash loan, or privileged role is required for the attacker.
- The bypass is reachable on every `exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput` call.

---

### Recommendation

The extension must gate the original user, not the intermediary. Two viable approaches:

1. **Router-forwarded identity in `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool. The extension decodes and verifies it, trusting only a known router address as the forwarder. This requires the extension to maintain a trusted-router registry.

2. **Separate `originator` field in the pool interface**: Add an `originator` parameter to `pool.swap()` that the router sets to `msg.sender`. The extension checks `originator` instead of `sender`. This is a breaking interface change but is the cleanest fix.

Until fixed, pools using `SwapAllowlistExtension` should not allowlist the router and should require direct pool interaction.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup:
// 1. Deploy pool with SwapAllowlistExtension
// 2. allowedSwapper[pool][alice]  = true   (approved user)
// 3. allowedSwapper[pool][router] = true   (so alice can use the router)
// 4. allowedSwapper[pool][bob]    = false  (bob is NOT approved)

function testBypassSwapAllowlistViaRouter() public {
    // alice is allowlisted, bob is not
    vm.prank(poolAdmin);
    swapAllowlist.setAllowedToSwap(address(pool), address(router), true);
    vm.prank(poolAdmin);
    swapAllowlist.setAllowedToSwap(address(pool), alice, true);
    // bob is NOT allowlisted

    // Bob calls the router directly — extension sees sender=router, which IS allowlisted
    vm.startPrank(bob);
    token0.approve(address(router), type(uint256).max);
    uint256 amountOut = router.exactInputSingle(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            recipient: bob,
            zeroForOne: true,
            amountIn: 1000e18,
            amountOutMinimum: 0,
            priceLimitX64: 0,
            deadline: block.timestamp + 1,
            extensionData: ""
        })
    );
    vm.stopPrank();

    // Bob's swap succeeded despite not being allowlisted
    assertGt(amountOut, 0, "Bob bypassed the swap allowlist via router");
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
