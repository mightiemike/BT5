### Title
`SwapAllowlistExtension` Gates Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument it receives from the pool. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so `sender` = router address. If the pool admin allowlists the router to permit router-mediated swaps, every unprivileged user can bypass the allowlist by calling the public router. The guard fires and passes, but it is checking the wrong identity — an exact structural analog to the FERC20 `burnFrom` bug where the Transfer event fires and the balance is decremented but the wrong accounting variable (`_totalSupply`) is left unchanged.

---

### Finding Description

`ExtensionCalling._beforeSwap` forwards `sender` (the pool's `msg.sender`) as the first argument to every registered extension: [1](#0-0) 

When a user calls `MetricOmmSimpleRouter.exactInput*`, the router calls `pool.swap(recipient, ...)`. Inside the pool, `msg.sender` is the router, so `sender` = `address(router)`. This value is what `SwapAllowlistExtension.beforeSwap` receives and checks against its per-pool allowlist.

The unit test confirms the extension checks the first argument as the swapper identity: [2](#0-1) 

The research pivot document explicitly flags this as the critical validation gap: [3](#0-2) 

There are two failure modes:

1. **Allowlist bypass (critical path):** Pool admin allowlists the router so that their approved users can swap via the router. Because the router is a public, permissionless contract, *any* address can call it and the extension sees `sender = router`, which is allowlisted. The allowlist is completely defeated.

2. **Allowlisted users locked out of router (secondary path):** Pool admin allowlists specific EOAs. Those EOAs can call `pool.swap()` directly and pass. But if they use the router, the router address is checked and is not allowlisted, so the call reverts. Legitimate users cannot use the standard periphery.

Both paths break the intended invariant. Path 1 is the fund-impacting one: unauthorized swappers gain access to a restricted pool.

---

### Impact Explanation

- **Broken core pool functionality:** The allowlist guard — the only access-control mechanism for swap-restricted pools — is rendered ineffective for all router-mediated swaps.
- **Unauthorized swap execution:** Any unprivileged user can swap against a pool that the admin intended to restrict, potentially draining LP value through arbitrage or front-running in a pool designed for a closed set of participants.
- **Admin-boundary break:** The pool admin configures an allowlist expecting it to gate real swappers; the actual gate is on a public intermediary contract, making the admin's configuration meaningless.

This meets the Metric OMM allowed impact gate: *Admin-boundary break — pool admin role check bypassed by an unprivileged path.*

---

### Likelihood Explanation

- Trigger is fully unprivileged: any address can call `MetricOmmSimpleRouter`.
- The bypass requires no special setup beyond the pool having `SwapAllowlistExtension` configured with the router allowlisted (the only configuration that makes router-mediated swaps work at all).
- No malicious initial pool setup is assumed; the pool admin acts in good faith and configures the allowlist as documented.

---

### Recommendation

`SwapAllowlistExtension.beforeSwap` must check the **original caller**, not the intermediary. Two options:

1. **Pass the original caller through the router:** `MetricOmmSimpleRouter` should accept a `swapper` parameter and forward it as `extensionData`; the extension decodes and checks it. This requires the router to be trusted to not forge the identity.

2. **Check `msg.sender` at the router boundary:** Require that the pool's `swap` is only callable directly (no router) when the allowlist is active, or have the router pass the real user address in a signed/verified way.

The cleanest fix is for the pool to pass the *original* `tx.origin`-equivalent through a trusted periphery path, or for the extension to decode the real swapper from `extensionData` that the router populates with `msg.sender` before forwarding.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup:
// 1. Deploy pool with SwapAllowlistExtension.
// 2. Admin allowlists the router so approved users can swap via router.
// 3. Unauthorized user (not on allowlist) calls router.exactInputSingle().

// Inside pool.swap():
//   msg.sender == address(router)   ← router is allowlisted
//   sender forwarded to extension   == address(router)
//   extension.isAllowedToSwap(pool, address(router)) == true  ← passes

// Result: unauthorized user's swap executes successfully.
// The allowlist guard fired but checked the wrong identity.
// Invariant broken: pool.swap executed for a swapper not on the allowlist.

function test_swapAllowlist_bypassViaRouter() public {
    // pool has SwapAllowlistExtension; router is allowlisted, attacker is not
    address attacker = makeAddr("attacker");
    deal(token0, attacker, 1e18);

    vm.startPrank(attacker);
    IERC20(token0).approve(address(router), type(uint256).max);

    // Direct swap reverts: attacker not allowlisted
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    pool.swap(attacker, false, 1000, type(uint128).max, "", "");

    // Router-mediated swap succeeds: router IS allowlisted
    // extension sees sender=router, not attacker
    (int128 d0, int128 d1) = router.exactInputSingle(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            zeroForOne: false,
            amountIn: 1000,
            amountOutMinimum: 0,
            priceLimitX64: type(uint128).max,
            recipient: attacker,
            extensionData: ""
        })
    );
    // Swap succeeded — allowlist bypassed
    assertTrue(d0 != 0 || d1 != 0);
    vm.stopPrank();
}
``` [1](#0-0) [4](#0-3) [3](#0-2)

### Citations

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

**File:** metric-periphery/test/extensions/SwapAllowlistSubExtension.t.sol (L26-48)
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

  function test_onlyPoolAdminCanSetSwappers() public {
    vm.prank(admin);
    extension.setAllowedToSwap(address(pool), swapper, true);
    assertTrue(extension.isAllowedToSwap(address(pool), swapper));

    vm.prank(swapper);
    vm.expectRevert(abi.encodeWithSelector(BaseMetricExtension.OnlyPoolAdmin.selector, address(pool), swapper, admin));
    extension.setAllowedToSwap(address(pool), swapper, false);
  }
```

**File:** generate_scanned_questions.py (L655-663)
```python
        Target(
            short="swap allowlist gate",
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```
