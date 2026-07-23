Based on my investigation of the codebase, I can identify a valid analog.

---

### Title
`SwapAllowlistExtension`: `beforeSwap` checks router address as `sender` instead of the actual end-user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the first argument forwarded by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so `sender` passed to the extension is the **router address**, not the actual end-user. If the pool admin allowlists the router (which is necessary to support any router-mediated swap), every user — including those not individually allowlisted — can bypass the per-user gate.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check as:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct) and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`:

```solidity
function _beforeSwap(address sender, address recipient, ...) internal {
    _callExtensionsInOrder(
        BEFORE_SWAP_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
    );
}
``` [2](#0-1) 

The pool passes its own `msg.sender` as `sender`. When `MetricOmmSimpleRouter` calls `pool.swap(...)`, the pool's `msg.sender` is the **router contract**, so the extension receives `sender = router`. The extension then checks `allowedSwapper[pool][router]` — not `allowedSwapper[pool][actual_user]`. [3](#0-2) 

This is structurally identical to the GolomTrader bug: the guard checks the wrong identity (router instead of user), so the authorization decision is made against an address that does not represent the economically relevant actor.

The project's own audit target document explicitly flags this as the critical validation focus:

> *"Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting. Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract."* [4](#0-3) 

---

### Impact Explanation

A pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers). To support any router-mediated swap at all, the admin must allowlist the router address. Once the router is allowlisted, **any user** — including those explicitly not on the allowlist — can call `MetricOmmSimpleRouter.exactInputSingle(...)`, which calls `pool.swap(...)` with `msg.sender = router`. The extension sees `sender = router`, finds it allowlisted, and permits the swap. The per-user gate is completely bypassed.

Consequence: unauthorized users execute swaps against a pool that was intended to be restricted, draining liquidity or extracting value at oracle-anchored prices that the pool admin intended to offer only to specific counterparties.

---

### Likelihood Explanation

- The router is a public, permissionless contract that any user can call.
- Any pool that uses `SwapAllowlistExtension` and also wants to support router-mediated swaps for its allowlisted users **must** allowlist the router — creating the bypass condition automatically.
- No privileged access, no special setup, and no malicious initial configuration is required. Any user can trigger this by calling the router.

---

### Recommendation

Pass the **original caller** (end-user) as `sender` to the extension, not the intermediate router. One approach: the router forwards the original `msg.sender` explicitly as part of `extensionData`, and the extension decodes it. Alternatively, the pool's `swap` function should accept an explicit `sender` parameter (the true originator) rather than using `msg.sender`, with the router passing `msg.sender` (the user) into that field. The `DepositAllowlistExtension` avoids this problem by checking `owner` (the LP position owner explicitly provided by the caller) rather than `sender`; the swap allowlist should adopt the same pattern.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `beforeSwap` order.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — necessary to allow any router-mediated swap for allowlisted users.
3. Pool admin does **not** call `setAllowedToSwap(pool, attacker, true)`.
4. Attacker (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
5. Router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. Pool calls `_beforeSwap(router, recipient, ...)`.
7. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Attacker successfully swaps against the restricted pool, bypassing the per-user allowlist. [3](#0-2) [5](#0-4)

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
