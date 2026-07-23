### Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist via the Public Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end-user. If the pool admin allowlists the router (the only way to permit router-mediated swaps), every unprivileged user can bypass the allowlist by calling through the public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap()
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol L162-176
abi.encodeCall(
  IMetricOmmExtensions.beforeSwap,
  (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the per-pool allowlist, using `msg.sender` (the pool) as the namespace key:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
  );
```

At this point `msg.sender` inside the pool is the **router address**, so the extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

For the router path to work at all, the pool admin must allowlist the router. Once the router is allowlisted, the check `allowedSwapper[pool][router] == true` passes for every caller of the router, regardless of who the actual end-user is. The allowlist is completely neutralised.

The same structural problem exists in the multi-hop `exactInput` path (line 104) and `exactOutput` path (line 165/220).

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict swaps to KYC'd or otherwise vetted addresses loses that protection entirely. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` (a public, permissionless contract) and the extension will see the allowlisted router address as `sender`, granting the swap. The attacker receives pool output tokens at the oracle-derived price, draining liquidity that was intended to be accessible only to vetted counterparties. This is a direct loss of LP principal and a complete bypass of the configured security boundary.

---

### Likelihood Explanation

The router is the standard, documented periphery entry point. Any pool admin who wants users to interact normally will allowlist the router. The bypass requires no special privileges, no flash loans, and no multi-block setup — a single `exactInputSingle` call from any EOA suffices. The condition (router allowlisted) is the expected production state, not an edge case.

---

### Recommendation

The extension must gate on the **economic actor**, not the immediate caller. Two sound approaches:

1. **Pass the original `msg.sender` through the router.** Add a `swapper` field to the swap call or use `extensionData` to carry the authenticated originator, and have the extension verify it against the allowlist. The pool or router must authenticate this field (e.g., the pool trusts only factory-registered routers to supply a forwarded sender).

2. **Check `recipient` instead of `sender` when the sender is a known router.** This is weaker but avoids the router-as-sender problem for simple single-hop cases.

The cleanest fix is for the pool to expose a `trustedForwarder` registry (similar to ERC-2771) so that when `msg.sender` is a registered router, the extension can recover the original user from `extensionData` with a verified signature or transient-storage context set by the router.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin allowlists only `alice` (a KYC'd address)
  - Pool admin also allowlists `router` (MetricOmmSimpleRouter) so alice can use the UI

Attack (by `bob`, a non-allowlisted address):
  1. bob calls router.exactInputSingle({pool: pool, recipient: bob, ...})
  2. Router calls pool.swap(bob, zeroForOne, amount, ...)
  3. Pool calls _beforeSwap(msg.sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  5. Swap executes; bob receives tokens
  6. Allowlist policy is bypassed with zero special access
```

The root cause is identical in structure to the zNS replay-attack analog: the identity bound to the authorization check (`sender` = router) does not uniquely identify the economic actor (`bob`) who benefits from the action, so the check provides no real protection once the intermediary is trusted. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
