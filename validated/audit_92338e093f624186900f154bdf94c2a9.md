### Title
SwapAllowlistExtension Checks Router Address as `sender` Instead of Actual Swapper, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

The `SwapAllowlistExtension.beforeSwap` hook receives `sender` as the address that called `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` is the **router's address**, not the actual user. A pool admin who allowlists the router (required for router-mediated swaps to function on an allowlisted pool) inadvertently grants every user the ability to bypass the per-user allowlist gate, allowing unauthorized swappers to trade against the pool and drain LP assets.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to every extension hook.**

`MetricOmmPool.swap()` calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` then ABI-encodes that value as the `sender` field forwarded to every configured extension: [2](#0-1) 

**Step 2 — The extension interface exposes `sender` as the identity to gate.**

`IMetricOmmExtensions.beforeSwap` declares `sender` as the first parameter, which is the identity the `SwapAllowlistExtension` is expected to check against its per-pool allowlist: [3](#0-2) 

**Step 3 — When a user routes through `MetricOmmSimpleRouter`, `sender` = router, not the user.**

`MetricOmmSimpleRouter` calls `pool.swap(...)` directly. From the pool's perspective, `msg.sender` is the router contract. The pool therefore passes the **router's address** as `sender` to `_beforeSwap`, and the `SwapAllowlistExtension` evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][actualUser]`.

**Step 4 — The dilemma that creates the bypass.**

A pool admin who wants to:
- Restrict swaps to a specific set of KYC'd or trusted addresses, **and**
- Allow those users to use the public `MetricOmmSimpleRouter`

must allowlist the router address. Once the router is allowlisted, **any** user — including those explicitly not on the allowlist — can bypass the gate by routing through the router. The extension's `allowedSwapper` mapping is keyed on `sender`, which collapses to a single router entry for all router-mediated swaps.

The research audit pivot for this path explicitly flags this: *"Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting"* and *"assert the hook cannot be bypassed by routing through an intermediate public contract."* [4](#0-3) 

---

### Impact Explanation

Any user can swap against a pool configured with `SwapAllowlistExtension` by routing through `MetricOmmSimpleRouter`, as long as the router is allowlisted. Pools that use the allowlist to restrict counterparties (e.g., permissioned RWA pools, KYC-gated venues, or pools with specific LP agreements) will have their access control silently nullified. Unauthorized swappers can execute trades against the pool's liquidity, causing direct loss of LP principal through unfavorable or adversarial swap execution.

This satisfies the **Broken core pool functionality causing loss of funds** and **Admin-boundary break: unprivileged path bypasses role checks** impact criteria.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the primary public entry point for swaps.
- Any pool admin who deploys a `SwapAllowlistExtension` and wants their allowlisted users to use the router **must** allowlist the router, triggering the bypass.
- No special privileges, flash loans, or exotic token behavior are required — a standard router call suffices.
- The bypass is reachable by any unprivileged user in a single transaction.

Likelihood: **Medium** (requires the common and expected admin action of allowlisting the router).

---

### Recommendation

The `SwapAllowlistExtension` must gate the **economically relevant actor** — the end user — not the intermediary router. Two sound mitigations:

1. **Forward the real user via `extensionData`**: The router encodes the original `msg.sender` into `extensionData` and the extension reads and verifies it (requires the extension to trust the router's encoding, which itself needs authentication).

2. **Check `recipient` instead of `sender`**: If the pool's design guarantees that `recipient` is always the actual beneficiary of the swap, the allowlist can gate on `recipient`. However, this must be verified against the router's call pattern.

3. **Transient-storage attribution**: The router writes the real caller into a transient slot before calling the pool; the extension reads that slot. This is the most tamper-resistant approach given the protocol already uses EIP-1153 transient storage for reentrancy guards. [5](#0-4) 

---

### Proof of Concept

```
Setup:
  1. Deploy MetricOmmPool with SwapAllowlistExtension as beforeSwap hook.
  2. Admin calls SwapAllowlistExtension.setAllowedSwapper(pool, router, true)
     — necessary so that allowlisted users can use the router.
  3. Admin does NOT call setAllowedSwapper(pool, attacker, true).

Attack:
  4. Attacker (not on allowlist) calls MetricOmmSimpleRouter.exactInput(...)
     targeting the pool.
  5. Router calls pool.swap(recipient=attacker, ...).
  6. Pool calls _beforeSwap(sender=router, ...).
  7. SwapAllowlistExtension checks allowedSwapper[pool][router] → true → PASS.
  8. Swap executes. Attacker receives pool output tokens.

Expected: revert (attacker not allowlisted).
Actual:   swap succeeds — allowlist bypassed via router.
```

The attacker pays only gas and the pool's swap fee, while LP assets are exposed to an unauthorized counterparty on every subsequent router-mediated swap.

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
