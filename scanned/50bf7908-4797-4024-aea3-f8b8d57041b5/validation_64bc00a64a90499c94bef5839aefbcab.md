### Title
`SwapAllowlistExtension` gates the router address instead of the real swapper, letting any unprivileged user bypass a curated pool's swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the actual user. If the pool admin allowlists the router (the natural action to let allowlisted users trade via the router), every unprivileged user can bypass the allowlist by routing through the same router.

---

### Finding Description

**Root cause — wrong actor checked**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument — the immediate caller of `pool.swap`: [3](#0-2) 

**Router path — sender is the router, not the user**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly; the pool therefore sees `msg.sender = router`: [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**The impossible admin choice**

| Admin configuration | Allowlisted user via router | Unauthorized user via router |
|---|---|---|
| Only user addresses allowlisted | ❌ blocked (router not listed) | ❌ blocked |
| Router also allowlisted | ✅ passes | ✅ **also passes — bypass** |

To let allowlisted users trade through the router the admin must allowlist the router address. Doing so simultaneously grants every unprivileged user the same permission.

---

### Impact Explanation

Any user can execute swaps on a pool whose admin intended to restrict trading to a curated set of addresses. The allowlist — the sole access-control boundary on the swap path — is silently voided for all router-mediated calls. Depending on the pool's purpose this enables unauthorized price-taking, adverse selection against LPs, or regulatory non-compliance. This is a direct admin-boundary break: an unprivileged path (`router → pool.swap`) bypasses a configured guard.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical user-facing swap entry point. Any pool operator who deploys a `SwapAllowlistExtension` and wants allowlisted users to trade via the router will naturally add the router to the allowlist. The bypass is then immediately reachable by any address with no special privileges or capital requirements.

---

### Recommendation

The extension must verify the **economically relevant actor**, not the immediate caller. Two sound approaches:

1. **Check `sender` only for direct pool calls; require an explicit user-identity forwarding mechanism for router calls** — e.g., the router passes the original `msg.sender` as `extensionData`, and the extension verifies it against a signature or a trusted-forwarder registry.
2. **Check `sender` and reject any call where `sender` is a known periphery contract** — allowlisted users must call the pool directly, and the router is never allowlisted.

The simplest safe fix is to remove the router from the allowlist and document that allowlisted users must call `pool.swap` directly, or to redesign the extension to accept a user-identity proof in `extensionData`.

---

### Proof of Concept

```
Setup
─────
1. Deploy pool with SwapAllowlistExtension.
2. Admin calls setAllowedToSwap(pool, alice, true)      // allowlist Alice
3. Admin calls setAllowedToSwap(pool, router, true)     // enable Alice to use router

Attack
──────
4. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, ...})
   → router calls pool.swap(recipient, ...) with msg.sender = router
   → pool calls extension.beforeSwap(router, ...)
   → extension checks allowedSwapper[pool][router] → true  ✓
   → swap executes for Bob with no revert

Invariant broken
────────────────
allowedSwapper[pool][bob] == false, yet Bob's swap succeeds.
``` [5](#0-4) [6](#0-5) [4](#0-3)

### Citations

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
