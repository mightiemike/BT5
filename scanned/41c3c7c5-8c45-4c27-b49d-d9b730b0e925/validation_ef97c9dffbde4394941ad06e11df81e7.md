Based on my investigation of the codebase, I can identify a valid analog. Let me present the finding.

---

### Title
SwapAllowlistExtension gates the router address instead of the actual swapper, allowing any user to bypass the allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

The `SwapAllowlistExtension.beforeSwap` hook receives `sender` as the identity to check against the allowlist. However, `sender` is always `msg.sender` of the `pool.swap()` call — which is `MetricOmmSimpleRouter` when users route through the periphery router. Because the router is a public, permissionless contract, any non-allowlisted user can bypass the per-user swap allowlist by routing through `MetricOmmSimpleRouter` instead of calling the pool directly.

### Finding Description

In `ExtensionCalling._beforeSwap`, the pool passes `msg.sender` as the `sender` argument to every registered extension: [1](#0-0) 

When `MetricOmmPool.swap()` is called directly by a user, `msg.sender` is the user. But when a user calls `MetricOmmSimpleRouter.exactInputSingle()` (or any `exact*` variant), the router calls `pool.swap()`, making `msg.sender` — and therefore `sender` in the hook — the **router's address**, not the actual user. [2](#0-1) 

The `SwapAllowlistExtension.beforeSwap` hook checks whether `sender` is in the `allowedSwapper` mapping (keyed by `(pool, sender)`). Since `sender` = router for all router-mediated swaps, the allowlist check is performed against the router's address, not the actual user's address.

This creates two failure modes:
1. **Bypass**: If the pool admin allowlists the router (necessary to allow any router-based swaps), every user — including non-allowlisted ones — can swap freely by going through the router.
2. **Lockout**: If the router is not allowlisted, all router-mediated swaps revert, even for users who are individually allowlisted.

The analog to the external `abs` bug is exact: just as `abs(type(int256).min)` silently returns the wrong value (the guard appears to work but produces an incorrect result), `SwapAllowlistExtension` silently checks the wrong identity (the router instead of the user), making the guard appear active while being completely ineffective. [3](#0-2) 

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict swaps to a specific set of addresses (e.g., KYC'd users, whitelisted market participants, or protocol-controlled addresses). Any non-allowlisted user can bypass this restriction entirely by routing through `MetricOmmSimpleRouter`, executing swaps on a pool that was designed to be access-controlled. This breaks the core allowlist invariant and constitutes an admin-boundary break with direct fund-impacting consequences (unauthorized parties can drain or manipulate restricted pools).

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Any user aware of the router can trivially bypass the allowlist. No special privileges, flash loans, or complex setup are required — a standard router call suffices. [4](#0-3) 

### Recommendation

The `SwapAllowlistExtension` must check the **actual end-user identity**, not the intermediary caller. Two approaches:

1. **Pass the real user via `extensionData`**: The router encodes the original `msg.sender` into `extensionData`, and the extension decodes and verifies it (with the router signing or the pool trusting only known routers).
2. **Check `recipient` instead of `sender`**: If the pool's design guarantees that `recipient` is always the actual user (not the router), the allowlist can gate on `recipient`. However, this must be verified against the full call path.

The root fix must ensure the identity checked by the allowlist is the economically relevant actor — the user initiating the trade — not the routing intermediary.

### Proof of Concept

```
1. Pool P is deployed with SwapAllowlistExtension E.
2. Admin allowlists the router address in E (required for any router-based swap to work).
3. Non-allowlisted user U calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...}).
4. Router calls P.swap(recipient=U, ...).
5. Pool calls _beforeSwap(sender=router, ...).
6. SwapAllowlistExtension.beforeSwap checks allowedSwapper[P][router] → true (router is allowlisted).
7. Swap proceeds. U successfully swaps on a pool they were never authorized to access.
```

The allowlist guard is bypassed without any privileged action, malicious token, or special setup — only a standard router call. [5](#0-4)

### Citations

**File:** metric-core/contracts/ExtensionCalling.sol (L75-86)
```text
  function _callExtensionsInOrder(uint256 order, bytes memory data) private {
    if (order == 0) return;

    while (true) {
      uint256 extensionIndex = order & 0x7;
      if (extensionIndex == 0) break;
      address extension = _extensionAddress(extensionIndex);
      if (extension == address(0)) revert PanicEmptyExtension();
      CallExtension.callExtension(extension, data);
      order >>= 3;
    }
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

**File:** metric-core/contracts/MetricOmmPool.sol (L281-295)
```text
    _afterSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      packedSlot0Final,
      bidPriceX64,
      askPriceX64,
      amount0Delta.toInt128(),
      amount1Delta.toInt128(),
      protocolFeeAmount,
      extensionData
    );
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L1-1)
```text
// SPDX-License-Identifier: MIT
```
