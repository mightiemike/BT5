### Title
SwapAllowlistExtension Bypass via Router: Any User Can Swap in Allowlisted Pools by Routing Through MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension sees the router's address — not the user's address — as the swapper identity. If the pool admin allowlists the router to enable router-mediated swaps, every user, including non-allowlisted ones, bypasses the per-user gate.

---

### Finding Description

**Step 1 — Pool passes its own `msg.sender` as `sender` to extensions.**

In `MetricOmmPool.swap()`, the `sender` forwarded to `_beforeSwap` is `msg.sender` of the pool call: [1](#0-0) 

**Step 2 — `SwapAllowlistExtension` checks that `sender` against the allowlist.**

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension's caller) and `sender` is whoever called `pool.swap()`. [2](#0-1) 

**Step 3 — The router calls `pool.swap()` directly, substituting itself as `sender`.**

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` with no mechanism to forward the original user's address: [3](#0-2) 

The same pattern holds for `exactInput` (multi-hop), `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

**Resulting identity mismatch:**

| Call path | `sender` seen by extension | Allowlist entry needed |
|---|---|---|
| User → `pool.swap()` directly | User address | `allowedSwapper[pool][user]` |
| User → `router.exactInputSingle()` → `pool.swap()` | Router address | `allowedSwapper[pool][router]` |

The extension has no way to distinguish individual users who route through the same router instance. The pool admin faces an impossible choice:

- **Do not allowlist the router** → all router-mediated swaps revert for every user, including allowlisted ones.
- **Allowlist the router** → every user on-chain can bypass the per-user gate by calling the router.

There is no configuration that simultaneously permits router-mediated swaps for allowlisted users and blocks non-allowlisted users.

---

### Impact Explanation

A pool admin who deploys a `SwapAllowlistExtension` to restrict swaps to a known set of addresses (e.g., KYC-verified counterparties, protocol-internal actors) and also allowlists the public `MetricOmmSimpleRouter` to support normal UX loses the allowlist entirely. Any address can call `router.exactInputSingle()` and the extension approves the swap because it sees the router — which is allowlisted — as the swapper. The allowlist guard is rendered inoperative, and the pool's access-control invariant is broken for all router-mediated swap paths.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. A pool admin who wants end-users to interact via the router must allowlist it. The allowlist bypass is therefore reachable in any realistic production deployment where the extension is used alongside the router. No privileged access, no malicious setup, and no non-standard tokens are required — any EOA can call the router.

---

### Recommendation

The `sender` identity forwarded to extensions must reflect the original economic actor, not the intermediate contract. Two complementary fixes:

1. **Router-side**: Have the router encode `msg.sender` (the real user) into `extensionData` before calling `pool.swap()`, and document this convention.
2. **Extension-side**: `SwapAllowlistExtension.beforeSwap()` should decode and verify the original user from `extensionData` when `sender` is a known router, or the pool should expose a dedicated "originator" field that periphery contracts populate.

Alternatively, restrict the allowlist check to direct pool calls only and require that any router used with allowlisted pools implements its own per-user gate before calling the pool.

---

### Proof of Concept

```
Setup:
  pool admin deploys pool with SwapAllowlistExtension
  pool admin calls extension.setAllowedToSwap(pool, alice, true)
    → alice is the only allowlisted swapper
  pool admin calls extension.setAllowedToSwap(pool, router, true)
    → router allowlisted so alice can use the UI

Attack (bob, not allowlisted):
  bob calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
       msg.sender of pool.swap() = router
    → pool calls extension.beforeSwap(sender=router, ...)
    → extension checks allowedSwapper[pool][router] == true  ✓
    → swap executes; bob receives output tokens

Result:
  bob, who is not in the allowlist, successfully swaps in a pool
  that the admin intended to restrict to alice only.
  The allowlist invariant is broken for every non-allowlisted
  address that routes through the public router.
``` [5](#0-4) [6](#0-5) [1](#0-0)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-41)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

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
