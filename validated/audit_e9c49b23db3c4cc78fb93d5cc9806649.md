### Title
`SwapAllowlistExtension` checks router address instead of original user, enabling full allowlist bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

The `SwapAllowlistExtension.beforeSwap` hook validates the `sender` parameter, which resolves to the `MetricOmmSimpleRouter` contract address — not the original EOA — when swaps are routed through the periphery. If the pool admin allowlists the router to enable router-mediated swaps for their curated users, any non-allowlisted user can bypass the allowlist by routing through the same public router contract.

---

### Finding Description

**Invariant broken**: A curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it. The `SwapAllowlistExtension` is designed to gate swaps by specific user addresses, but the identity it checks changes depending on the entrypoint used.

**Root cause — wrong actor binding in `beforeSwap`:**

`SwapAllowlistExtension.beforeSwap` performs:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` is the pool (the extension is called by the pool via `_callExtensionsInOrder`) and `sender` is the first parameter forwarded by the pool. [1](#0-0) 

**Call path — how `sender` is bound:**

In `MetricOmmPool.swap`, the pool calls:

```solidity
_beforeSwap(
    msg.sender,   // ← this is the direct caller of pool.swap()
    recipient,
    ...
)
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` then encodes this as the `sender` argument forwarded to every configured extension: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle(...)`, the router calls `pool.swap(...)` directly:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [4](#0-3) 

So `msg.sender` inside `pool.swap` is the **router address**, not the original EOA. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`.

**The inescapable trap for pool admins:**

The pool admin cannot simultaneously achieve all three goals:

| Goal | Action required | Side effect |
|---|---|---|
| Allow `alice` to swap directly | Allowlist `alice` | Only `alice` can swap directly |
| Allow `alice` to use the router | Allowlist the router | **Every user** can now swap through the router |
| Block `bob` from using the router | Do not allowlist the router | `alice` also cannot use the router |

There is no configuration that allows specific users to use the router while blocking others, because the router is a shared public contract and the extension cannot distinguish between different EOAs behind it.

---

### Impact Explanation

A non-allowlisted user can swap on a curated pool by routing through `MetricOmmSimpleRouter`. The allowlist guard — the pool's primary access-control mechanism — is completely bypassed. LPs who deployed the pool specifically for curated counterparties (e.g., KYC'd users, institutional partners) are exposed to unauthorized swaps. Depending on pool configuration, this can result in:

- Unauthorized extraction of LP value through arbitrage or directional trading
- Disruption of the pool's intended market dynamics
- Direct loss of LP principal if the pool's pricing assumptions depend on the curated counterparty set

This matches the allowed impact gate: **admin-boundary break** (pool admin's allowlist policy bypassed by an unprivileged path) and **broken core pool functionality** (swap access control fails open for the router entrypoint).

---

### Likelihood Explanation

The bypass requires the pool admin to allowlist the router. This is a natural and expected action: any pool admin who wants their allowlisted users to access multi-hop routing, exact-output swaps, or deadline-protected swaps via `MetricOmmSimpleRouter` must allowlist the router. The admin may not realize this simultaneously opens the pool to all users. The trigger is therefore reachable through normal, well-intentioned pool administration.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the economically relevant actor — the original EOA — not the intermediary contract. Two viable approaches:

1. **Router-side**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` (e.g., as a leading 20-byte prefix), and have `SwapAllowlistExtension.beforeSwap` decode and check that address when `sender` is a known router.

2. **Extension-side**: Add a registry of trusted forwarders to `SwapAllowlistExtension`. When `sender` is a registered forwarder, decode the real user from `extensionData` and apply the allowlist check against that address instead.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  alice  → allowedSwapper[pool][alice]  = true
  router → allowedSwapper[pool][router] = true   ← admin adds this to let alice use the router

Attack (bob, not allowlisted):
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  router calls pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
  pool calls _beforeSwap(msg.sender=router, ...)
  SwapAllowlistExtension.beforeSwap checks allowedSwapper[pool][router] → true
  ✓ check passes — bob's swap executes on the curated pool
```

The check `allowedSwapper[pool][router]` is `true` because the admin allowlisted the router for `alice`'s benefit. `bob` is never checked. The allowlist is fully bypassed. [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L227-240)
```text
    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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
