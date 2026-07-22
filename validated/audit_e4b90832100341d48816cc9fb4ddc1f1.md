### Title
`SwapAllowlistExtension` gates on the router's address instead of the original user when swaps are routed through `MetricOmmSimpleRouter`, making the allowlist unconfigurable for router-mediated swaps — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`, `metric-core/contracts/ExtensionCalling.sol`)

---

### Summary

`ExtensionCalling._beforeSwap` passes `msg.sender` of the pool call as `sender` to every configured extension. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool boundary is the **router contract**, not the original user. `SwapAllowlistExtension.beforeSwap` checks `isAllowedToSwap(pool, sender)` against this router address. This creates an irresolvable configuration trap: the allowlist cannot simultaneously (a) block non-allowlisted users who route through the router and (b) allow allowlisted users who route through the router, because both groups present the same `sender` identity to the hook.

---

### Finding Description

In `ExtensionCalling._beforeSwap`, the `sender` argument forwarded to every extension is hardcoded to `msg.sender` of the pool's `swap()` call: [1](#0-0) 

When `MetricOmmSimpleRouter` executes a swap on behalf of a user, it calls `pool.swap(...)` directly, making `msg.sender = router`. The pool then passes `sender = router` into `_beforeSwap`, which dispatches it to `SwapAllowlistExtension.beforeSwap`. [2](#0-1) 

`SwapAllowlistExtension` exposes `isAllowedToSwap(pool, swapper)` and its `beforeSwap` hook evaluates that check against the `sender` it receives — which is the router address, not the original user: [3](#0-2) 

This produces two mutually exclusive failure modes with no correct configuration path:

**Mode A — Allowlisted users are silently blocked through the router.** If the pool admin populates the allowlist with specific user addresses but does not add the router, every router-mediated swap reverts even for legitimately allowlisted users. The standard periphery path (`MetricOmmSimpleRouter`) is broken for the pool's intended participants.

**Mode B — Disallowed users bypass the allowlist through the router.** If the admin adds the router to the allowlist (the natural fix for Mode A), `isAllowedToSwap(pool, router)` returns `true` for every caller regardless of their individual allowlist status. Any address — including explicitly blocked users — can bypass the curation policy by routing through `MetricOmmSimpleRouter`.

The pool's `swap()` function passes `msg.sender` as `sender` to the after-swap hook as well, so the same identity mismatch propagates to `_afterSwap`: [4](#0-3) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC-verified addresses, specific market makers, or whitelisted institutions) loses that restriction entirely once the router is added to the allowlist. Any unprivileged user can execute swaps on the restricted pool by routing through `MetricOmmSimpleRouter`, receiving output tokens at the pool's oracle-anchored price. This is a direct bypass of the pool's core access-control invariant and constitutes broken core pool functionality with direct fund-flow consequences (disallowed parties drain liquidity at pool prices).

---

### Likelihood Explanation

The trigger is fully unprivileged: any user can call `MetricOmmSimpleRouter.exactInputSingle()` or equivalent. The only precondition is that the pool admin has added the router to the allowlist — a natural and expected administrative action for any pool that intends to support router-mediated trading for its allowlisted users. The research document for this codebase explicitly identifies this exact actor-identity mismatch as a primary audit target, confirming the configuration path is realistic. [5](#0-4) 

---

### Recommendation

The pool's `swap()` function should accept an explicit `payer` or `originator` parameter that the router populates with `msg.sender` before calling the pool, and `ExtensionCalling._beforeSwap` should forward that value as `sender` to extensions instead of `msg.sender`. Alternatively, `SwapAllowlistExtension.beforeSwap` should decode the original user from `extensionData` when the immediate caller is a known periphery contract. The invariant that must hold: the address checked against the allowlist must be the address that economically controls the swap input, not the intermediate dispatcher.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured; add `userA` to the allowlist; do **not** add the router.
2. `userA` calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`. The router calls `pool.swap(recipient, ...)`. `msg.sender` at the pool is the router. `SwapAllowlistExtension.beforeSwap` evaluates `isAllowedToSwap(pool, router)` → `false` → revert. `userA` cannot use the standard periphery path despite being allowlisted. (**Mode A**)
3. Admin adds the router to the allowlist to fix Mode A.
4. `userB` (not on the allowlist, explicitly blocked) calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`. The router calls `pool.swap(...)`. `msg.sender` at the pool is the router. `SwapAllowlistExtension.beforeSwap` evaluates `isAllowedToSwap(pool, router)` → `true` → swap proceeds. `userB` receives pool output tokens despite being a blocked address. (**Mode B**)

The root cause is in `ExtensionCalling._beforeSwap` passing `msg.sender` (the router) as `sender` rather than the originating user: [6](#0-5)

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

**File:** metric-periphery/contracts/interfaces/extensions/ISwapAllowlistExtension.sol (L10-18)
```text
  function allowedSwapper(address pool, address swapper) external view returns (bool);

  function allowAllSwappers(address pool) external view returns (bool);

  function setAllowedToSwap(address pool, address swapper, bool allowed) external;

  function setAllowAllSwappers(address pool, bool allowed) external;

  function isAllowedToSwap(address pool, address swapper) external view returns (bool);
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
