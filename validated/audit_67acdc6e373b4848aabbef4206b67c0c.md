### Title
`SwapAllowlistExtension` Gates the Router Contract Instead of the Actual User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The allowlist therefore gates the router's address rather than the actual user's address. If the pool admin allowlists the router to enable router-mediated swaps, any unprivileged user can bypass the individual allowlist entirely.

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ...)          // msg.sender at pool = router
             → _beforeSwap(msg.sender=router, ...)
                 → ExtensionCalling._beforeSwap(sender=router, ...)
                     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                         → checks allowedSwapper[pool][router]   ← NOT the user
```

In `MetricOmmPool.swap()`, `msg.sender` (the immediate caller) is forwarded as `sender` to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` encodes this value and dispatches it to every configured extension:

```solidity
// ExtensionCalling.sol:162-165
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, ...)   // sender = router
)
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The check is `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Two broken invariants result:**

1. **Allowlist bypass:** If the pool admin allowlists the router address (a natural step to enable router-mediated swaps for their curated users), every unprivileged user can bypass the individual allowlist by calling the router. The router is a public, permissionless contract with no caller restrictions.

2. **Broken core flow:** If the pool admin only allowlists individual EOA addresses, those allowlisted users cannot swap through the router at all (the router is not in the allowlist). They are forced to call the pool directly, bypassing the intended user-facing periphery.

### Impact Explanation

**Scenario A — Allowlist bypass (High):** A pool admin configures `SwapAllowlistExtension` to restrict trading to a curated set of users and allowlists the router to enable the standard swap flow. Any non-allowlisted user calls `router.exactInputSingle(...)`. The pool sees `sender = router`, which is allowlisted, and the swap executes. The curated access control is completely defeated. Any user can trade on a pool that was intended to be restricted.

**Scenario B — Broken core functionality (Medium):** A pool admin allowlists specific EOA addresses but does not allowlist the router. Those allowlisted users call the router (the standard interface) and receive `NotAllowedToSwap`. The router-mediated swap path is permanently broken for all allowlisted users on that pool.

Both scenarios represent a direct mismatch between the identity the allowlist was designed to gate and the identity it actually checks.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool that deploys `SwapAllowlistExtension` and expects users to interact through the router will encounter one of the two broken states above. The bypass scenario (Scenario A) is reachable by any unprivileged user with zero preconditions beyond the pool admin having allowlisted the router — a configuration that is necessary to make the router work at all on an allowlisted pool.

### Recommendation

The `beforeSwap` hook should gate the **actual end user**, not the immediate pool caller. Two viable approaches:

1. **Pass the original user through `extensionData`:** The router encodes the original `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between the router and the extension.

2. **Check `recipient` instead of `sender`:** For swap allowlists, the economically relevant actor is the recipient of output tokens. Gating `recipient` instead of `sender` would correctly identify the user in router-mediated flows, though it changes the semantic of the allowlist.

3. **Require direct pool calls for allowlisted pools:** Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the factory or extension configuration level.

### Proof of Concept

```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Pool admin allowlists the router (required to enable router swaps)
ext.setAllowedToSwap(address(pool), address(router), true);
// Pool admin does NOT allowlist attacker
// ext.setAllowedToSwap(address(pool), attacker, false); // default

// Attacker bypasses allowlist via router
vm.prank(attacker);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: token0,
        tokenOut: token1,
        zeroForOne: true,
        amountIn: 1_000,
        amountOutMinimum: 0,
        recipient: attacker,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// Swap succeeds: allowedSwapper[pool][router] == true
// attacker is not individually allowlisted but trades anyway
```

The pool's `beforeSwap` hook receives `sender = address(router)`, which is allowlisted, so the check passes and the non-allowlisted attacker executes the swap. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
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
