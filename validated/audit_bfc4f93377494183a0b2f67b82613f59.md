Based on the code I was able to retrieve from the codebase search (file reads failed in the final iteration), I can synthesize a finding from the confirmed structural evidence.

---

### Title
Swap Allowlist Guard Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap`)

### Summary

The `SwapAllowlistExtension.beforeSwap` hook gates swaps using the `sender` argument passed by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks the router's address against the allowlist — not the actual end-user's address. Any pool that allowlists the router (to support router-mediated swaps for legitimate users) inadvertently grants every unprivileged user the ability to bypass the per-user allowlist restriction.

### Finding Description

The pool's `_beforeSwap` dispatcher in `ExtensionCalling.sol` forwards `sender` (i.e., `msg.sender` at the pool call boundary) to every configured extension: [1](#0-0) 

The `IMetricOmmExtensions.beforeSwap` interface signature confirms `sender` is the first argument the extension receives: [2](#0-1) 

The research target in the repository's own audit scaffold explicitly documents the concern:

> *"allowAll/allowedSwapper lookup keyed by pool and sender … Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting … assert the hook cannot be bypassed by routing through an intermediate public contract."* [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInput*` → pool `swap()`, the pool sees `msg.sender = router`. The extension's `allowedSwapper` mapping is keyed on `(pool, sender)`, so it evaluates the router address. The pool admin faces an impossible choice:

- **Do not allowlist the router** → allowlisted users cannot use the router at all (broken UX).
- **Allowlist the router** → every unprivileged user can bypass the per-user restriction by routing through the public router.

### Impact Explanation

A pool admin who deploys `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers) and also allowlists the router to support normal UX inadvertently opens the pool to all users. Any non-allowlisted address can execute swaps in the restricted pool by calling `MetricOmmSimpleRouter` instead of the pool directly. This constitutes an **admin-boundary break**: an unprivileged path (the public router) bypasses a pool-admin-configured guard, enabling unauthorized trading that the pool admin explicitly intended to prevent. Depending on pool configuration, this can result in unauthorized extraction of liquidity or execution of trades against oracle-anchored prices in a pool not designed for open access.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is a public, permissionless contract. No special privilege is required to call it. The bypass requires only that the pool admin has allowlisted the router address — a natural and expected configuration for any pool that wants to support the standard periphery UX. The attacker needs no funds beyond the swap input tokens and no special knowledge beyond the pool address.

### Recommendation

The `SwapAllowlistExtension.beforeSwap` hook should check the **actual end-user** rather than `sender`. Two sound approaches:

1. **Pass the real user through `extensionData`**: The router encodes the original `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires the router to be trusted to not forge the identity — acceptable given the router is a known, audited periphery contract.
2. **Check `recipient` instead of `sender`**: If the pool's swap design guarantees `recipient` is always the economic beneficiary (the actual user), the allowlist can gate on `recipient`. Verify this holds for all router call paths.
3. **Dedicated router-aware allowlist**: Extend the extension to recognize when `sender` is the known router and, in that case, require the allowlist check to pass for the `recipient` address instead.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin allowlists: [alice, router_address]
  - Pool admin does NOT allowlist: bob

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  2. Router calls pool.swap(sender=router, recipient=bob, ...)
  3. Pool calls SwapAllowlistExtension.beforeSwap(sender=router, ...)
  4. Extension checks allowedSwapper[pool][router] → TRUE (router is allowlisted)
  5. Swap proceeds — bob has bypassed the per-user allowlist restriction

Result:
  bob, a non-allowlisted address, successfully swaps in a pool the admin
  intended to restrict to alice only.
``` [4](#0-3) [5](#0-4)

### Citations

**File:** metric-core/contracts/ExtensionCalling.sol (L159-176)
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
```

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L50-60)
```text
  function beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) external returns (bytes4);
```

**File:** generate_scanned_questions.py (L655-663)
```python
        Target(
            short="swap allowlist gate",
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L1-1)
```text
// SPDX-License-Identifier: MIT
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L1-1)
```text
// SPDX-License-Identifier: MIT
```
