### Title
`SwapAllowlistExtension.beforeSwap` gates on the router address instead of the actual end-user when swaps are routed through `MetricOmmSimpleRouter`, allowing any user to bypass the per-user allowlist if the router is allowlisted — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is designed to restrict which addresses may swap in a pool. Its `beforeSwap` hook checks the `sender` argument passed by the pool, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension sees the router address as the swapper — not the actual end-user. If the pool admin allowlists the router (a natural step to enable router-based swaps for their allowlisted users), every user who calls through the router bypasses the individual allowlist entirely.

---

### Finding Description

**Allowlist check identity:**

In `SwapAllowlistExtension.beforeSwap`, the gated identity is `sender`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (enforced by `onlyPool`). `sender` is the first argument forwarded by the pool from `ExtensionCalling._beforeSwap`, which is `msg.sender` of `pool.swap()`.

**Pool passes its own `msg.sender` as `sender`:**

```solidity
// metric-core/contracts/MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

**Router always calls `pool.swap()` as itself:**

In `exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput`, the router calls `IMetricOmmPoolActions(pool).swap(...)` directly. For every hop, `msg.sender` of `pool.swap()` is the router contract, not the end-user:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol:72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

For multi-hop `exactInput`, this holds for every hop:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol:104-112
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
    .swap(
        i == last ? params.recipient : address(this),
        ...
    );
```

**The resulting identity mismatch:**

| Entry path | `sender` seen by extension | Allowlist check |
|---|---|---|
| User calls `pool.swap()` directly | User address | Correct |
| User calls `router.exactInputSingle()` | Router address | Wrong — checks router, not user |
| User calls `router.exactInput()` (any hop) | Router address | Wrong — checks router, not user |

**Bypass scenario:**

1. Pool admin deploys a restricted pool with `SwapAllowlistExtension` and adds Alice and Bob to `allowedSwapper[pool]`.
2. Alice and Bob try to swap through the router — they are blocked because the router is not in the allowlist.
3. Pool admin adds the router to `allowedSwapper[pool]` to fix this.
4. Now Mallory (not in the allowlist) calls `router.exactInputSingle()` → the extension sees `sender` = router → router is allowlisted → Mallory's swap succeeds.

The allowlist is now completely ineffective for router-mediated swaps.

---

### Impact Explanation

The `SwapAllowlistExtension` is a core access-control guard. Its bypass allows any unprivileged user to swap in a pool that was intended to be restricted (e.g., to KYC'd counterparties, institutional traders, or specific protocol integrations). Unauthorized swaps in a restricted pool can:

- Extract value from LPs who deposited under the assumption that only vetted counterparties would trade against them.
- Violate regulatory or contractual access restrictions the pool admin intended to enforce.
- Break the pool's intended trading model (e.g., a pool designed for a single trusted market maker).

This is a broken core pool functionality with direct fund-impact potential: LP assets are exposed to swaps from actors the pool was explicitly configured to exclude.

---

### Likelihood Explanation

The bypass requires the pool admin to allowlist the router. This is a realistic and predictable operational step: any pool admin who wants their allowlisted users to be able to use the standard router will add the router to the allowlist, not realizing this opens the pool to all router users. The design offers no safe middle ground — either allowlisted users cannot use the router, or all users can bypass the allowlist through the router.

---

### Recommendation

The `SwapAllowlistExtension` should gate on the actual end-user, not the direct caller of `pool.swap()`. Two approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires the extension to trust the router's encoding, which introduces its own trust assumptions.

2. **Check `sender` (direct caller) AND require the router to forward the real user**: The router could be modified to pass the real user as a verified field, and the extension could verify the router's identity before trusting the forwarded address.

3. **Gate on `sender` only for direct pool calls; reject router-mediated calls unless the router is explicitly trusted and forwards the real user identity**: The extension could maintain a separate `trustedRouter` mapping and decode the real user from `extensionData` when `sender` is a trusted router.

The simplest safe fix is to document that the allowlist only works for direct pool calls and that router-mediated swaps bypass per-user gating — and to add a check in the extension that reverts if `sender` is a known router unless `allowAllSwappers` is set.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension, only Alice is allowlisted
extension.setAllowedToSwap(address(pool), alice, true);

// Alice tries to swap through the router — REVERTS (router not allowlisted)
vm.prank(alice);
router.exactInputSingle(ExactInputSingleParams({pool: address(pool), ...}));
// → NotAllowedToSwap (router address not in allowlist)

// Admin adds router to fix Alice's access
vm.prank(admin);
extension.setAllowedToSwap(address(pool), address(router), true);

// Alice can now swap through router ✓
vm.prank(alice);
router.exactInputSingle(...); // succeeds

// Mallory (NOT allowlisted) also swaps through router — SUCCEEDS (bypass)
vm.prank(mallory);
router.exactInputSingle(ExactInputSingleParams({pool: address(pool), ...}));
// → succeeds: extension sees sender=router, router is allowlisted
// Mallory has bypassed the per-user allowlist
```

The root cause is in `SwapAllowlistExtension.beforeSwap` checking `sender` (the router) instead of the actual end-user, with no mechanism to recover the real user identity from the call context. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-125)
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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
