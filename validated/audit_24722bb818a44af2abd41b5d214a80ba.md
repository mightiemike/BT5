### Title
`SwapAllowlistExtension` checks the router's address instead of the real user's address, allowing any actor to bypass the swap allowlist by routing through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the `pool.swap()` call. When a user enters through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the real user. The allowlist therefore checks whether the **router** is permitted, not whether the **user** is permitted. Because the router is a public, permissionless contract, any actor can bypass the allowlist by routing through it.

---

### Finding Description

**Call chain (direct swap — allowlist works correctly):**
```
User → pool.swap(...)
         msg.sender = User
         _beforeSwap(sender = User, ...)
         SwapAllowlistExtension.beforeSwap(sender = User)
         check: allowedSwapper[pool][User]   ✓ correct actor
```

**Call chain (router-mediated swap — allowlist checks wrong actor):**
```
User → MetricOmmSimpleRouter.exactInputSingle(...)
         router → pool.swap(recipient, ...)
                    msg.sender = router
                    _beforeSwap(sender = router, ...)
                    SwapAllowlistExtension.beforeSwap(sender = router)
                    check: allowedSwapper[pool][router]   ✗ wrong actor
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap` directly, making itself the pool's `msg.sender`: [4](#0-3) 

This creates an irresolvable dilemma for the pool admin:

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | Router-mediated swaps fail for **all** users, including allowlisted ones — broken functionality |
| Router **allowlisted** | **Any** user can bypass the allowlist by routing through the public router — security bypass |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

A pool deployer configures `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, protocol-owned addresses, or whitelisted market makers). Any non-allowlisted user can bypass this restriction entirely by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutputSingle` / `exactOutput`). The router is a public, permissionless contract with no access control of its own. The bypassing user can execute arbitrary swaps on the restricted pool, draining LP value at oracle-quoted prices that the pool admin intended to reserve for specific counterparties. This is a direct loss of LP principal and a complete failure of the intended access-control boundary.

---

### Likelihood Explanation

The exploit requires no special privileges, no flash loans, and no multi-step setup. Any user who can call `MetricOmmSimpleRouter` — which is a public periphery contract — can bypass the allowlist on any pool that has configured `SwapAllowlistExtension` and allowlisted the router. The router is the canonical swap entry point documented for end users, so the bypass path is the default path for any user who follows standard integration guidance.

---

### Recommendation

The allowlist must gate the **original user**, not the intermediary contract. Two complementary fixes:

1. **Pass the real user through the router.** Have `MetricOmmSimpleRouter` forward the original `msg.sender` as an authenticated `sender` field inside `extensionData`, and have `SwapAllowlistExtension.beforeSwap` decode and verify it. This requires a trust relationship between the extension and the router (e.g., the extension only accepts the decoded sender when `msg.sender` — the pool — was called by a known router).

2. **Check `sender` and `recipient` together.** Alternatively, the extension can require that `sender` is either directly allowlisted or is a known router whose `extensionData` carries a signed or authenticated user identity.

The simplest safe fix that preserves the current architecture is to check the `recipient` (the address that receives output tokens) rather than `sender` when the pool is called by a known router, since `recipient` is always the real beneficiary of the swap and cannot be spoofed by the router.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup: pool deployed with SwapAllowlistExtension as beforeSwap hook.
// allowedUser is allowlisted; attacker is NOT allowlisted.
// The router is allowlisted so that allowedUser can use it.

function test_swapAllowlistBypassViaRouter() external {
    // Confirm attacker is blocked on direct swap
    vm.prank(attacker);
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    pool.swap(attacker, false, int128(1000), type(uint128).max, "", "");

    // Allowlist the router so allowedUser can use it
    vm.prank(poolAdmin);
    swapAllowlistExtension.setAllowedToSwap(address(pool), address(router), true);

    // Attacker bypasses allowlist by routing through the public router
    // The extension sees sender = router (allowlisted), not attacker (not allowlisted)
    vm.prank(attacker);
    token0.approve(address(router), type(uint256).max);
    router.exactInputSingle(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            tokenIn: address(token0),
            recipient: attacker,
            zeroForOne: false,
            amountIn: 1000,
            amountOutMinimum: 0,
            priceLimitX64: type(uint128).max,
            deadline: block.timestamp + 1,
            extensionData: ""
        })
    );
    // Swap succeeds — attacker received token0 output despite not being allowlisted
}
```

The `SwapAllowlistExtension` checks `allowedSwapper[pool][router]` (true) instead of `allowedSwapper[pool][attacker]` (false), so the guard passes and the non-allowlisted attacker executes a swap on the restricted pool.

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
