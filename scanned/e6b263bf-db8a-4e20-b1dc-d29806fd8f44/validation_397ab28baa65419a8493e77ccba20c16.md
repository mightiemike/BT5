### Title
SwapAllowlistExtension Checks Router Address Instead of User Identity, Allowing Any User to Bypass the Swap Allowlist on Curated Pools - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. The pool always passes `msg.sender` (the immediate caller of `pool.swap()`) as `sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the user. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, every unpermissioned user can bypass the allowlist by routing through the public router.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` is allowlisted for the calling pool (`msg.sender` = pool):

```solidity
// SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol:72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
```

The pool receives `msg.sender = router`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The actual user's identity is never verified.

**Concrete bypass path:**
1. Pool admin deploys a curated pool with `SwapAllowlistExtension` as a `beforeSwap` hook.
2. Admin allowlists specific users: `setAllowedToSwap(pool, user1, true)`.
3. Admin also allowlists the router so allowlisted users can use the standard periphery: `setAllowedToSwap(pool, router, true)`.
4. Non-allowlisted attacker calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap()` → pool calls `_beforeSwap(msg.sender=router, ...)`.
6. Extension checks `allowedSwapper[pool][router] = true` → passes.
7. Attacker executes a swap on a pool they are explicitly excluded from.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` in the router.

### Impact Explanation

Any user can trade on a curated pool by routing through the public `MetricOmmSimpleRouter`. The allowlist guard is completely neutralized whenever the router is allowlisted. LPs in the curated pool are exposed to trades from actors the pool admin explicitly intended to exclude, which can result in direct loss of LP principal through unfavorable or adversarial swaps that the allowlist was designed to prevent.

### Likelihood Explanation

High. The `MetricOmmSimpleRouter` is the standard, documented periphery entry point for swaps. Pool admins who configure a swap allowlist and want their allowlisted users to be able to use the router (the normal user flow) must allowlist the router address. This is the expected operational pattern, and it silently opens the gate to all users. No privileged access or malicious setup is required — any user with a standard router call can exploit this.

### Recommendation

The extension must verify the economically relevant actor, not the immediate pool caller. Two options:

1. **Check `tx.origin` as a fallback** (fragile, not recommended for general use).
2. **Require the router to forward the original user identity** in `extensionData`, and have the extension decode and verify it — but this requires router cooperation and is not enforced by the current interface.
3. **Preferred**: Change `SwapAllowlistExtension.beforeSwap` to check the `recipient` or require the pool to pass the original initiator. Alternatively, document that the router must never be allowlisted and that allowlisted users must call the pool directly — but this breaks the standard UX.

The cleanest fix is to have the router pass the original `msg.sender` as a verified field in `extensionData` and have the extension decode it, with the pool or router signing/attesting the identity. Alternatively, the allowlist should gate on `recipient` if the intent is to restrict who receives output tokens.

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension as beforeSwap hook
  admin calls: swapExtension.setAllowedToSwap(pool, router, true)
  admin calls: swapExtension.setAllowedToSwap(pool, alice, true)
  // bob is NOT allowlisted

Attack:
  bob calls: router.exactInputSingle({pool: pool, recipient: bob, ...})
  → router calls pool.swap(recipient=bob, ...) with msg.sender=router
  → pool calls _beforeSwap(sender=router, ...)
  → extension checks allowedSwapper[pool][router] == true → PASSES
  → bob's swap executes on the curated pool despite not being allowlisted

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
