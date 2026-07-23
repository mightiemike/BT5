### Title
`SwapAllowlistExtension` checks the router address instead of the originating user, making the allowlist bypassable via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap` is the **router contract**, not the originating user. The extension therefore checks whether the router is allowlisted, not whether the user is allowlisted. This creates an irreconcilable dilemma: if the pool admin allowlists the router to permit router-mediated swaps, every unpermissioned user can bypass the allowlist; if the admin does not allowlist the router, every individually-allowlisted user is silently blocked from using the standard periphery.

---

### Finding Description

**Call chain when a user swaps through the router:**

```
user EOA
  → MetricOmmSimpleRouter.exactInputSingle(...)
      → IMetricOmmPoolActions(pool).swap(recipient, ...)   // msg.sender = router
          → MetricOmmPool._beforeSwap(msg.sender=router, ...)
              → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                  checks: allowedSwapper[pool][router]   ← wrong actor
```

The pool passes `msg.sender` as the `sender` argument to every extension hook: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

The router calls `pool.swap` directly, with no mechanism to forward the originating user's identity: [4](#0-3) 

The existing test suite confirms the allowlist is keyed to the **immediate caller** of `pool.swap` (the `TestCaller` contract), not the EOA behind it: [5](#0-4) 

No test exercises a router-mediated swap against a `SwapAllowlistExtension`-protected pool, so the bypass is untested and undetected.

---

### Impact Explanation

**Scenario A — bypass (High):** Pool admin allowlists the router address so that router-mediated swaps work. Because the extension checks the router, every unpermissioned user can now swap by routing through `MetricOmmSimpleRouter`. The allowlist is completely ineffective. Any user can drain liquidity from a pool that was intended to be curated (e.g., KYC-gated, institutional-only), causing direct loss of LP principal through bad-price execution or unauthorized access to privileged liquidity.

**Scenario B — broken core functionality (High):** Pool admin does not allowlist the router. Every individually-allowlisted user who calls `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` is silently blocked with `NotAllowedToSwap`, even though they are explicitly permitted. The standard periphery is unusable for any allowlisted pool, breaking the core swap flow.

Both outcomes are contest-relevant: Scenario A is a direct allowlist-bypass with fund-impacting consequences; Scenario B is broken core pool functionality.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool that deploys `SwapAllowlistExtension` and expects users to interact through the router will immediately encounter one of the two failure modes. The trigger requires no special privilege — any public user can call the router. The pool admin's only "fix" (allowlisting the router) is itself the bypass vector.

---

### Recommendation

The extension must check the economically relevant actor, not the immediate caller. Two options:

1. **Pass the originating user through `extensionData`:** The router encodes `msg.sender` (the originating user) into `extensionData` before calling `pool.swap`. The extension decodes and checks that address. This requires a convention between router and extension.

2. **Check `recipient` instead of `sender`:** For swap allowlists, the `recipient` (the address receiving output tokens) is often the economically relevant actor and is already forwarded to the extension. However, `recipient` can be set to any address, so this is only safe if the pool's design equates recipient with the authorized swapper.

3. **Structural fix:** Add an `originSender` field to the `beforeSwap` hook signature that the pool populates from a trusted transient-storage slot set by the router, analogous to how Uniswap v4 passes `msgSender` through the unlock mechanism.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup:
// 1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
// 2. Pool admin allowlists alice (individual user) but NOT bob.
// 3. Pool admin also allowlists the router so alice can use it.
// 4. Bob (not allowlisted) calls router.exactInputSingle targeting the pool.
// 5. Extension sees sender = router (allowlisted) → swap succeeds for bob.

function test_swapAllowlist_bypass_via_router() public {
    // alice is allowlisted, bob is not
    swapExtension.setAllowedToSwap(address(pool), alice, true);
    // admin allowlists router so alice can use it
    swapExtension.setAllowedToSwap(address(pool), address(router), true);

    // bob (not allowlisted) routes through the router
    vm.prank(bob);
    token0.approve(address(router), type(uint256).max);

    // Expect: should revert NotAllowedToSwap — but it SUCCEEDS
    vm.prank(bob);
    router.exactInputSingle(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            tokenIn: address(token0),
            tokenOut: address(token1),
            zeroForOne: true,
            amountIn: 1000,
            amountOutMinimum: 0,
            priceLimitX64: 0,
            recipient: bob,
            deadline: block.timestamp + 1,
            extensionData: ""
        })
    );
    // bob received token1 despite not being allowlisted
}
```

The root cause is that `SwapAllowlistExtension` checks `sender` (the router) rather than the originating user, making the guard meaningless for any pool that permits router-mediated access. [3](#0-2) [1](#0-0) [4](#0-3)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-176)
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
