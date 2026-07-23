### Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the real swapper, allowing any user to bypass the curated-pool allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is designed to restrict swaps on a curated pool to a specific set of approved addresses. However, the `beforeSwap` hook checks the `sender` argument supplied by the pool, which is always `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router contract**, not the end user. If the pool admin allowlists the router (a natural action to enable router-mediated swaps for approved users), every user on-chain can bypass the allowlist by routing through the router.

---

### Finding Description

**Call chain that exposes the bug:**

```
user (attacker) → MetricOmmSimpleRouter.exactInputSingle()
                → pool.swap(recipient, ...)          // msg.sender = router
                → _beforeSwap(msg.sender=router, ...) // ExtensionCalling.sol:231
                → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                → allowedSwapper[pool][router] == true → passes
```

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to every before-swap hook:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← always the immediate caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension:

```solidity
// ExtensionCalling.sol:162-176
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)  // sender = pool's msg.sender
)
```

`SwapAllowlistExtension.beforeSwap` then checks that forwarded value:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct), and `sender` is whoever called `pool.swap()`. When the router is the caller, `sender = router`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**The bypass scenario:**

1. Pool admin deploys a curated pool with `SwapAllowlistExtension`.
2. Admin allowlists specific users: `setAllowedToSwap(pool, alice, true)`.
3. Alice wants to use the router, so the admin also allowlists the router: `setAllowedToSwap(pool, router, true)`.
4. Now **any** user — including `bob` who is not allowlisted — calls `router.exactInputSingle({pool: pool, ...})`.
5. The extension sees `sender = router`, finds `allowedSwapper[pool][router] = true`, and passes.
6. Bob's swap executes on the curated pool, bypassing the intended per-user gate.

The pool admin has no way to simultaneously allow alice to use the router and block bob from using the same router, because the extension cannot distinguish between the two at the `sender` level.

---

### Impact Explanation

LP providers on a curated pool deposit liquidity expecting only approved counterparties to trade against them. Once the router is allowlisted (a prerequisite for any approved user to use the router), the allowlist is completely nullified for all router-mediated swaps. Any unprivileged user can drain liquidity from the curated pool by routing through `MetricOmmSimpleRouter`, exposing LP principal to unauthorized trading activity. This is a direct loss of LP assets above Sherlock thresholds on any pool that relies on `SwapAllowlistExtension` for access control.

---

### Likelihood Explanation

The trigger requires the pool admin to allowlist the router address. This is a natural and expected administrative action: any pool admin who wants their approved users to access the router (the primary supported swap interface) must allowlist it. The admin has no documented warning that doing so opens the gate to all users. The bypass is then reachable by any unprivileged user with zero additional preconditions.

---

### Recommendation

The `sender` argument passed to `beforeSwap` must represent the **original transaction initiator**, not the immediate caller of `pool.swap()`. Two complementary fixes:

1. **In the router**: propagate the original `msg.sender` through `extensionData` so the extension can recover the real user identity. The extension would then decode and check that value instead of `sender`.

2. **In `SwapAllowlistExtension`**: document clearly that `sender` is the immediate pool caller (router, not user) and that allowlisting a router grants access to all router users — or redesign the check to use a user-identity field passed through `extensionData`.

The cleanest fix is to have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and gate on that value when present.

---

### Proof of Concept

```solidity
// Scenario: curated pool with SwapAllowlistExtension
// Admin allowlists alice AND the router (so alice can use the router)
swapExtension.setAllowedToSwap(address(pool), alice, true);
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Bob (not allowlisted) bypasses the allowlist via the router
vm.prank(bob);
// This succeeds — extension sees sender=router, which IS allowlisted
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        recipient: bob,
        tokenIn: token0,
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: type(uint128).max,
        deadline: block.timestamp,
        extensionData: ""
    })
);
// Bob successfully swapped on a pool he was never approved for
```

**Root cause chain:**

- `MetricOmmPool.swap` passes `msg.sender` (router) as `sender` to `_beforeSwap` [1](#0-0) 

- `ExtensionCalling._beforeSwap` forwards it unchanged to the extension [2](#0-1) 

- `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the router, not the user [3](#0-2) 

- `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no user-identity forwarding [4](#0-3)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
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
