Based on my investigation of the codebase, here is my analysis:

---

### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

The `SwapAllowlistExtension.beforeSwap` hook gates swaps by checking the `sender` argument passed by the pool. When a user routes through the public `MetricOmmSimpleRouter`, the pool's `swap()` receives `msg.sender = router`, so the extension checks the router address against the allowlist — not the actual user. If the pool admin allowlists the router (required for any legitimate user to swap through it), every unpermissioned user can bypass the curated allowlist by routing through the router.

### Finding Description

`ExtensionCalling._beforeSwap` encodes and forwards `sender` (the pool's `msg.sender`) to every configured extension: [1](#0-0) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap(...)` directly. At that point `msg.sender` to the pool is the router contract, so `sender` forwarded to `beforeSwap` is the router address, not the originating user.

`SwapAllowlistExtension.beforeSwap` is described in the research scaffold as performing an "allowedSwapper lookup keyed by pool and sender": [2](#0-1) 

Because the check is on `sender` (= router address), the extension never sees the real user. The pool admin must allowlist the router for any legitimate user to swap through it. Once the router is allowlisted, the allowlist is effectively open to everyone.

The `BaseMetricExtension.onlyPool` modifier correctly restricts who can *call* the extension, but it does nothing to verify that the `sender` argument represents the economically relevant actor: [3](#0-2) 

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers). Any unpermissioned user can bypass this restriction by calling `MetricOmmSimpleRouter.exactInputSingle()` instead of calling the pool directly. The user receives tokens from the pool at oracle-derived prices with no allowlist enforcement. This is a direct loss of the pool admin's curation policy and exposes LP funds to unrestricted toxic flow.

**Severity: High** — direct bypass of a core access-control invariant with no additional preconditions beyond using the public router.

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is a public, permissionless contract.
- No special role, privileged key, or malicious setup is required.
- The attacker only needs to call `exactInputSingle()` on the router pointing at the allowlisted pool.
- The pool admin cannot prevent this without removing the router from the allowlist, which breaks legitimate user access. [4](#0-3) 

### Recommendation

The `SwapAllowlistExtension.beforeSwap` hook must check the **originating user**, not the intermediary router. Two approaches:

1. **Pass the real user through `extensionData`**: The router encodes the original `msg.sender` into `extensionData`; the extension decodes and checks it. This requires the router to be trusted to supply honest data.

2. **Check `recipient` instead of `sender`**: If the pool's swap function sets `recipient` to the actual beneficiary (the user), the extension can gate on `recipient`. This is only correct if `recipient` is always the economic beneficiary and cannot be spoofed.

3. **Preferred — use a dedicated allowlist keyed on the real caller**: Require the router to pass the originating user as a verified parameter (e.g., via a signed permit or a trusted forwarder pattern), and have the extension verify that parameter rather than `sender`.

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured. Only address `Alice` is allowlisted.
2. Pool admin allowlists `MetricOmmSimpleRouter` so Alice can use the router (necessary for normal UX).
3. Attacker `Bob` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({tokenIn, tokenOut, pool, ...})`.
4. Router calls `pool.swap(sender=router, recipient=Bob, ...)`.
5. Pool calls `SwapAllowlistExtension.beforeSwap(sender=router, ...)`.
6. Extension checks: `allowedSwapper[pool][router] == true` → passes.
7. Bob receives tokens from the pool. The allowlist was never consulted for Bob's address. [1](#0-0) [5](#0-4)

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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L31-35)
```text
  modifier onlyPoolAdmin(address pool_) {
    address poolAdmin = IMetricOmmPoolFactory(FACTORY).poolAdmin(pool_);
    if (msg.sender != poolAdmin) revert OnlyPoolAdmin(pool_, msg.sender, poolAdmin);
    _;
  }
```
