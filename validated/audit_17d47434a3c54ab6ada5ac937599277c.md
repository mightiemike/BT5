### Title
`SwapAllowlistExtension` Checks Direct Caller Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps using the `sender` argument, which is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the router's address, not the actual user's address. If the pool admin allowlists the router (required for any allowlisted user to use the router), every user — including non-allowlisted ones — can bypass the allowlist by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(...)` directly: [4](#0-3) 

So the extension sees `sender = router address`, not the actual end user. The allowlist check becomes `allowedSwapper[pool][router]`.

This creates an inescapable dilemma for the pool admin:

| Router allowlisted? | Allowlisted user can use router? | Non-allowlisted user can bypass? |
|---|---|---|
| No | No (reverts) | No |
| Yes | Yes | **Yes — full bypass** |

To give allowlisted users access to the router, the admin must allowlist the router. But once the router is allowlisted, any user can call `router.exactInputSingle(pool, ...)` and the extension approves the swap because it only sees the router's address.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., KYC-verified users, whitelist-only LPs, or exclusive market participants) can be fully bypassed by any unprivileged user routing through the public `MetricOmmSimpleRouter`. The non-allowlisted user receives tokens from the pool at oracle-derived prices, draining pool reserves in a way the pool admin explicitly intended to prevent. This breaks the core curation invariant of the allowlist extension and constitutes a direct loss of LP assets to unauthorized counterparties.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is a public, permissionless contract — any user can call it.
- The bypass requires only that the router is allowlisted, which is a necessary operational step for any allowlisted user to use the router.
- No privileged access, no special tokens, no malicious setup is required. A single `exactInputSingle` call suffices.
- Pool admins deploying `SwapAllowlistExtension` for compliance or access-control purposes are the primary target, and the bypass is invisible to them once the router is allowlisted.

---

### Recommendation

The `SwapAllowlistExtension` must gate the **actual end user**, not the direct caller of `pool.swap()`. Two approaches:

1. **Check `recipient` instead of `sender`** — for single-hop swaps the recipient is the actual beneficiary, though this breaks for multi-hop paths where the router is the intermediate recipient.

2. **Preferred: pass the original user through `extensionData`** — the router should encode the actual `msg.sender` into `extensionData`, and the extension should decode and verify it. This requires a coordinated change to the router and extension.

3. **Alternatively: gate at the router level** — add an allowlist check inside the router before calling `pool.swap()`, and document that the pool's allowlist must include the router address only when the router enforces its own user-level check.

---

### Proof of Concept

```
Setup:
  - Pool deployed with SwapAllowlistExtension
  - Pool admin allowlists alice (alice is the only intended swapper)
  - Pool admin also allowlists router (so alice can use the router)

Attack:
  1. bob (not allowlisted) calls:
       router.exactInputSingle({
         pool: pool,
         recipient: bob,
         zeroForOne: true,
         amountIn: X,
         ...
       })
  2. Router calls pool.swap(bob, true, X, ...) — msg.sender = router
  3. Pool calls extension.beforeSwap(router, bob, ...)
  4. Extension checks: allowedSwapper[pool][router] == true  ✓
  5. Swap executes — bob receives tokens from the pool
  6. Allowlist is bypassed; bob trades in a pool he was never authorized to access
``` [3](#0-2) [5](#0-4)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
