### Title
SwapAllowlistExtension gates the router address instead of the actual user when swaps are routed through MetricOmmSimpleRouter, allowing any user to bypass the allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is the pool's `msg.sender` — the router contract — not the original end-user. When the pool admin allowlists `MetricOmmSimpleRouter` to permit router-mediated swaps, every user on the network can bypass the allowlist by routing through the router.

---

### Finding Description

The call chain for a router-mediated swap is:

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → IMetricOmmPoolActions(pool).swap(recipient, ...)   // msg.sender = router
              → MetricOmmPool._beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  ← checked, NOT the user
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool passed — the router: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` calls the pool, the pool's `msg.sender` is the router: [4](#0-3) 

The pool admin faces an impossible choice:
- **Allowlist the router** → every user on the network can swap, defeating the allowlist entirely.
- **Do not allowlist the router** → no user can ever swap through the router on this pool.

There is no configuration that simultaneously permits router-mediated swaps and enforces per-user allowlist policy.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers, or protocol-internal actors) is completely unenforceable for any user who routes through `MetricOmmSimpleRouter`. Any unprivileged address can execute swaps against the pool by calling the public router, draining LP value or executing trades the pool admin explicitly intended to block. This is a direct admin-boundary break with fund-impacting consequences for LP principals.

---

### Likelihood Explanation

The router is the primary user-facing entry point documented and deployed alongside the protocol. Any production pool that uses `SwapAllowlistExtension` and expects normal user interaction through the router will be misconfigured by design. The bypass requires no special privileges, no flash loans, and no multi-step setup — a single `exactInputSingle` call suffices.

---

### Recommendation

The `SwapAllowlistExtension` must gate the **original end-user**, not the intermediate router. Two viable approaches:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated convention between router and extension.

2. **Check `sender` only when it is not a known router, and require the router to attest the real caller**: The extension maintains a registry of trusted routers and, when `sender` is a trusted router, reads the real caller from a standardized field in `extensionData`.

The simplest safe fix for the current architecture is option 1: the router always appends the original `msg.sender` to `extensionData`, and the extension decodes it before performing the allowlist lookup.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // must do this for any router swap to work
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker (not allowlisted) calls:
      router.exactInputSingle({pool: pool, tokenIn: ..., ...})
  - Router calls pool.swap(...) with msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - Swap executes successfully for the non-allowlisted attacker

Result:
  - attacker completes a swap on a pool that was supposed to block them.
  - The allowlist invariant is broken for every router-mediated swap.
``` [5](#0-4) [4](#0-3)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
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
```
