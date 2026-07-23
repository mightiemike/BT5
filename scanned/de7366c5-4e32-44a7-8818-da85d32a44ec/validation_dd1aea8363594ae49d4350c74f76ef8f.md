### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Enabling Allowlist Bypass - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` validates the `sender` argument forwarded by the pool, which equals `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the **router contract**, not the actual end user. If the router is allowlisted (the only way to let users use it), the allowlist is bypassed for every user. This is the direct analog of the external PDA report: the wrong identity is bound to the guard, so the guard authorizes the wrong actor.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` encodes that value and forwards it verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInput*`, the router calls `pool.swap(...)` directly. At that point the pool's `msg.sender` is the **router address**, so the extension checks `allowedSwapper[pool][router]` — not `allowedSwapper[pool][actualUser]`. [4](#0-3) 

The pool documentation explicitly acknowledges the operator pattern for liquidity but the swap path has no equivalent user-forwarding mechanism: [5](#0-4) 

---

### Impact Explanation

A pool admin who deploys a restricted pool (e.g., KYC-only, institutional-only) and configures `SwapAllowlistExtension` faces an inescapable dilemma:

- **If the router is NOT allowlisted**: every allowlisted user who tries to swap through `MetricOmmSimpleRouter` is rejected (`NotAllowedToSwap`), breaking the primary user-facing swap path.
- **If the router IS allowlisted** (the only practical fix): `allowedSwapper[pool][router] = true` passes for every call through the router, so **any address** — including explicitly blocked ones — can execute swaps by routing through the public router contract.

The second scenario is a complete allowlist bypass: non-allowlisted users execute swaps in a pool designed to restrict them, which can violate compliance requirements and allow unauthorized parties to extract value from restricted liquidity.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing entry point documented and tested throughout the periphery. Any pool that (a) configures `SwapAllowlistExtension` and (b) wants users to use the router will inevitably allowlist the router, triggering the bypass. The attacker needs no special privilege — a standard `exactInputSingle` call through the public router suffices.

---

### Recommendation

The extension must check the **actual end user**, not the intermediary. Two approaches:

1. **Pass the original caller through the router**: Have `MetricOmmSimpleRouter` encode the real `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check it. This requires a trusted forwarding convention.

2. **Check `sender` only when the caller is a known trusted router; otherwise check `msg.sender` of the pool call directly**: The extension can maintain a registry of trusted routers and fall back to the raw `sender` for direct pool calls.

The cleanest fix mirrors the deposit path's `owner` pattern: the pool should accept an explicit `swapper` parameter (the real user) separate from `msg.sender` (the operator/router), and pass that to extensions.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin allowlists only `alice` via setAllowedToSwap(pool, alice, true)
  - Pool admin also allowlists the router: setAllowedToSwap(pool, router, true)
    (required so alice can use the router)

Attack:
  - `bob` (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  - Router calls pool.swap(recipient=bob, ...)
  - Pool passes msg.sender=router as `sender` to _beforeSwap
  - SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  - Bob's swap executes successfully despite not being allowlisted

Result:
  - The swap allowlist is completely bypassed for any user routing through
    the public MetricOmmSimpleRouter
  - Pool admin's intent to restrict swaps to alice only is violated
``` [3](#0-2) [1](#0-0)

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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L111-113)
```text
  /// @notice Swap allowlist rejected `msg.sender`.
  /// @dev Only `swap` checks this when `SWAP_ALLOWLIST_PROVIDER` is set; `simulateSwapAndRevert` does not, so a passing simulation does not imply an allowed live swap.
  error NotAllowedToSwap();
```
