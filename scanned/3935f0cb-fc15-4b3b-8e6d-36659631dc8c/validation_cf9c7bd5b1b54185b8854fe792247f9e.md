### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User on Router-Mediated Swaps, Enabling Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against the per-pool allowlist. `sender` is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` of the pool call, so the extension checks the **router's address** instead of the **actual user's address**. If the router is allowlisted (a natural configuration for pools that want to support router-mediated swaps), any unprivileged user bypasses the allowlist entirely.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that argument against the allowlist:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

Here `msg.sender` is the pool (correct), and `sender` is whoever called `pool.swap()`.

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutputSingle` / `exactOutput`), the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol
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

The pool's `msg.sender` is the **router**, not the user. The extension therefore evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`.

Two concrete consequences follow:

1. **Allowlist bypass (High):** If the pool admin allowlists the router address (a natural step when the pool is meant to be accessible via the standard periphery), every unprivileged user can bypass the individual allowlist by routing through `MetricOmmSimpleRouter`. The extension sees the router as the swapper and passes the check.

2. **Broken core functionality (Medium):** If the router is *not* allowlisted, individually allowlisted users cannot use the router at all — their swaps revert with `NotAllowedToSwap` even though they are explicitly permitted. The only path left is a direct `pool.swap()` call, which defeats the purpose of the periphery layer.

---

### Impact Explanation

**Direct loss / broken invariant:** A curated pool's allowlist is its primary access-control boundary. Bypassing it lets non-KYC'd or otherwise excluded addresses trade on a pool that was explicitly configured to exclude them. This can drain LP value through arbitrage or front-running that the allowlist was designed to prevent, constituting a direct loss of LP principal and protocol fees.

**Severity: High** — the allowlist invariant is fully broken for any pool that allowlists the router, and the router is the standard, documented periphery entry point.

---

### Likelihood Explanation

Pool admins who deploy a curated pool and want users to interact via the standard `MetricOmmSimpleRouter` will naturally add the router to the allowlist. The `generate_scanned_questions.py` research file explicitly flags this path:

> *"Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."*

The router is a public, permissionless contract. Any user can call it. Once the router is allowlisted, the bypass is trivially reachable by any address.

---

### Recommendation

The extension must gate the **economically relevant actor** — the end user — not the intermediary router. Two approaches:

1. **Pass the original caller through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated convention between router and extension.

2. **Check `recipient` instead of `sender` for swap allowlists:** The recipient is the address that receives output tokens and is the economically relevant party. However, this changes semantics for multi-hop paths where the router is the intermediate recipient.

3. **Preferred — dedicated router forwarding:** Add a `swapFor(address realUser, ...)` pattern or a trusted-forwarder mechanism so the pool can distinguish the originating user from the routing intermediary.

---

### Proof of Concept

```
Setup:
  - Pool P configured with SwapAllowlistExtension E
  - Pool admin calls E.setAllowedToSwap(P, router, true)   // allowlist the router
  - Pool admin does NOT allowlist attacker address

Attack:
  1. Attacker (non-allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})
  2. Router calls P.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)
     → pool's msg.sender = router
  3. Pool calls _beforeSwap(msg.sender=router, ...)
  4. ExtensionCalling dispatches to E.beforeSwap(sender=router, ...)
  5. E checks: allowedSwapper[P][router] == true  → passes
  6. Swap executes; attacker receives output tokens

Result: Non-allowlisted attacker successfully swaps on a curated pool.
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
