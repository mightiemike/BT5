### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, allowing any user to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` — the **direct caller of `pool.swap()`**. When a user routes through `MetricOmmSimpleRouter`, the direct caller is the router contract, not the user. A pool admin who allowlists the router address (the natural configuration for router-mediated pools) inadvertently opens the gate to every user, defeating the per-user access control the extension is designed to enforce.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every registered extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is the entity that calls `pool.swap()`: [4](#0-3) 

So `sender` arriving at the extension is the **router address**, not the actual user. The same is true for every router entry point (`exactInput`, `exactOutputSingle`, `exactOutput`), where the pool's `msg.sender` is always the router. [5](#0-4) 

A pool admin who wants to support router-mediated swaps for a restricted set of users has two choices, both broken:

1. **Allowlist the router** — the extension passes for every user who calls the router, because the check is `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. Any unprivileged user bypasses the allowlist.
2. **Do not allowlist the router** — allowlisted users cannot use the router at all; their swaps revert because the router is not in the allowlist.

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, protocol-owned addresses, or whitelisted market makers) can be bypassed by any user who routes through `MetricOmmSimpleRouter`. The bypass is unconditional once the router is allowlisted: the extension never sees the real user identity, so it cannot enforce the intended per-user gate. Unauthorized users can execute swaps against the pool's liquidity, draining LP value through spread fees paid to the wrong parties or moving the pool price in ways the admin intended to prevent.

---

### Likelihood Explanation

The router is the standard, documented user-facing entry point for swaps. Any pool admin who deploys a swap-allowlisted pool and also wants users to interact via the router will naturally allowlist the router address. The misconfiguration is the expected outcome of following the normal integration path. No privileged escalation or malicious setup is required — a regular user simply calls `exactInputSingle` on the router.

---

### Recommendation

The extension must gate on the **economic actor**, not the intermediary. Two complementary fixes:

1. **In `SwapAllowlistExtension.beforeSwap`**: check `recipient` (the address receiving output tokens) or require the router to forward the originating user. Since the interface already passes both `sender` and `recipient`, the extension could check both, or the pool admin could document that `sender` is the direct caller.

2. **Preferred — router-level forwarding**: `MetricOmmSimpleRouter` should encode the originating `msg.sender` into `extensionData` for each hop, and `SwapAllowlistExtension` should decode and check that value when present. This mirrors how Uniswap v4 passes the `hookData` originator.

3. **Minimum viable fix**: document clearly that `sender` is the direct pool caller, and require pool admins to allowlist the router only when they intend to allow all users. Add a `setAllowedToSwapViaRouter` path that checks the decoded originator from `extensionData`.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowlisted
  - Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted for UX

Attack:
  - charlie (not allowlisted) calls router.exactInputSingle(pool, ...)
  - router calls pool.swap(recipient=charlie, ...)
  - pool calls extension.beforeSwap(sender=router, ...)
  - extension checks allowedSwapper[pool][router] == true  → passes
  - charlie's swap executes against the pool's liquidity

Expected: revert NotAllowedToSwap
Actual:   swap succeeds; charlie bypasses the per-user allowlist
```

The root cause is identical in structure to the EIP-4626 analog: a configured limit (`allowedSwapper` per user) is not consulted for the actual constrained entity (the real user) because the implementation reads a proxy value (the router address) that does not reflect the intended constraint. [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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
