### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User — Allowlist Fully Bypassed When Router Is Permitted - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router's address**, not the actual end-user. If the pool admin allowlists the router address to enable router-mediated swaps, every unprivileged user on the network can bypass the per-user allowlist gate by calling the public router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly: [4](#0-3) 

At that point `msg.sender` inside `pool.swap()` is the **router contract**, so `sender` delivered to the extension is the router's address — not the original user's address. The extension has no visibility into who called the router.

**The broken invariant:** A pool admin who wants to allow router-mediated swaps must call `setAllowedToSwap(pool, router, true)`. Once that entry exists, `allowedSwapper[pool][router] == true` for every call that arrives through the router, regardless of who the actual caller is. Any unprivileged address can therefore bypass the per-user allowlist by routing through the public `MetricOmmSimpleRouter`. [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, institutional LPs, or protocol-internal actors) loses that restriction entirely for any user who routes through the public `MetricOmmSimpleRouter`. The attacker receives pool output tokens they are not entitled to receive, and the pool's LP assets are exposed to unrestricted trading — a direct loss of the guard's protective value over LP principal and fee revenue.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is a public, permissionless contract. Any user can call `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` at any time. No special role, token balance, or prior interaction is required. The only precondition is that the pool admin has allowlisted the router (a natural operational step when the pool is meant to be accessible via the standard periphery). Likelihood is **High**.

---

### Recommendation

The extension must verify the identity of the **economic actor**, not the immediate caller of `pool.swap()`. Two sound approaches:

1. **Encode the real user in `extensionData`**: Have the router append `abi.encode(msg.sender)` to `extensionData` before forwarding to the pool, and have `SwapAllowlistExtension.beforeSwap` decode and check that value. The extension should revert if the decoded address is `address(0)` (i.e., the call did not come through a trusted router).

2. **Gate on `msg.sender` of the router, not the pool**: Introduce a trusted-forwarder pattern where the router stores the real caller in transient storage and the extension reads it via a known interface, similar to how the router already stores callback context.

Either way, the extension must not treat the router address as the swapper identity.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured.
2. Admin calls setAllowedToSwap(pool, router, true)
   — intending to allow router-mediated swaps for allowlisted users.
3. Attacker (address NOT in allowedSwapper) calls:
       router.exactInputSingle({pool: pool, ...})
4. Router calls pool.swap(recipient, ...) — msg.sender = router.
5. pool._beforeSwap(router, ...) → extension.beforeSwap(sender=router, ...)
6. allowedSwapper[pool][router] == true → check passes.
7. Attacker receives output tokens; allowlist is fully bypassed.
```

The attacker's address never appears in any allowlist check. The only address checked is the router, which the admin was forced to allowlist to enable normal router usage.

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-29)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
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
