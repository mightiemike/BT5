### Title
SwapAllowlistExtension gates on the router's address instead of the actual swapper, allowing any user to bypass the per-user swap allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. When a user swaps through `MetricOmmSimpleRouter`, the pool receives `msg.sender = router`, so the extension checks the router's address — not the actual user's address. If the pool admin allowlists the router (which is required for any router-mediated swap to succeed on an allowlisted pool), every unprivileged user can bypass the per-user restriction by routing through the public router contract.

---

### Finding Description

`ExtensionCalling._beforeSwap` forwards `sender` (the pool's `msg.sender`) to every configured extension: [1](#0-0) 

The pool's `swap()` entry point sets `sender = msg.sender`. When `MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()`, `msg.sender` is the router contract, so `sender` delivered to `beforeSwap` is the router address, not the originating user.

`SwapAllowlistExtension.beforeSwap` performs an `allowAll/allowedSwapper` lookup keyed on `(pool, sender)`: [2](#0-1) 

Because the lookup key is the router address, the check passes for every user the moment the pool admin allowlists the router — which is the only way to permit any router-based swap on an allowlisted pool. The pool admin faces an impossible choice:

- **Allowlist the router** → every unprivileged user bypasses the per-user restriction.
- **Do not allowlist the router** → no user can swap through the router at all.

The `IMetricOmmExtensions.beforeSwap` interface confirms `sender` is the first argument and is the only identity the extension receives for the swap initiator: [3](#0-2) 

The `generate_scanned_questions.py` audit target explicitly flags this path: *"Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."* [4](#0-3) 

---

### Impact Explanation

A pool admin who deploys a `SwapAllowlistExtension` to restrict swaps to trusted counterparties (e.g., whitelisted market makers, KYC'd users, or protocol-controlled routers) loses that protection entirely once the public `MetricOmmSimpleRouter` is allowlisted. Any unprivileged address can execute swaps against the pool at live oracle prices, exposing LPs to adverse selection, toxic flow, or regulatory non-compliance that the allowlist was designed to prevent. This is a direct admin-boundary break with LP-fund-impacting consequences.

---

### Likelihood Explanation

The trigger is fully unprivileged: any user who calls `MetricOmmSimpleRouter.exactInputSingle()` on a pool whose admin has allowlisted the router reaches the bypass. The `MetricOmmSimpleRouter` is a public, permissionless contract: [5](#0-4) 

No special role, flash loan, or callback is required. The only precondition is that the pool admin has allowlisted the router — a configuration that is necessary for any legitimate router-based swap to work on the pool.

---

### Recommendation

Pass the originating user's address through the router to the pool, and have the pool forward it to extensions as a separate `origin` argument, or require the router to embed the real user address in `extensionData` and have `SwapAllowlistExtension` decode and check that field instead of `sender`. Alternatively, the extension should check `msg.sender` of the extension call (which is always the pool) and require the pool to attest the real user identity via a verified `extensionData` payload signed or forwarded by the router.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — required for any router swap.
3. Pool admin does **not** call `setAllowedToSwap(pool, attacker, true)`.
4. `attacker` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. Pool calls `_beforeSwap(sender=router, ...)`.
7. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][router]` → `true` → passes.
8. Attacker's swap executes against the pool despite never being individually allowlisted. [1](#0-0) [6](#0-5)

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L1-1)
```text
// SPDX-License-Identifier: MIT
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L1-1)
```text
// SPDX-License-Identifier: MIT
```

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L188-195)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external returns (int128 amount0Delta, int128 amount1Delta);
```
