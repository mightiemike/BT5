### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Enabling Allowlist Bypass - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. A pool admin who allowlists the router to enable router-mediated swaps for their curated users inadvertently opens the pool to every user who calls the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim as the first positional argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks that argument against the per-pool allowlist: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap()` directly: [4](#0-3) 

From the pool's perspective `msg.sender` is the **router contract**, not the end user. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

Two harmful configurations follow:

1. **Allowlist bypass (high impact):** The pool admin allowlists the router address so that allowlisted users can reach the pool through the standard periphery. Because the check is keyed on the router, every user who calls the router passes the check, regardless of whether they are individually allowlisted.

2. **Broken functionality (medium impact):** The pool admin allowlists individual users but not the router. Those users cannot use the router at all; they must call the pool directly. The router — the protocol's own standard swap path — is effectively unusable for any curated pool.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties loses that restriction entirely the moment the router is allowlisted. Any unprivileged user can execute swaps against the pool's LP reserves at oracle-derived prices, draining LP value through adverse selection or simply trading in a market the pool admin intended to keep private. This is a direct loss of LP principal and a complete failure of the allowlist invariant.

---

### Likelihood Explanation

The router is the protocol's standard swap entry point. Pool admins who want their allowlisted users to benefit from slippage protection, multi-hop routing, and deadline checks will naturally add the router to the allowlist. The misconfiguration is not obvious because the admin's intent ("allow the router for my users") and the actual effect ("allow the router for everyone") diverge silently — no error is raised and no event distinguishes the two cases.

---

### Recommendation

Pass the **original user** through the call chain rather than the immediate `msg.sender`. Two concrete approaches:

1. **Router-side:** Have the router pass the original `msg.sender` as a verified field inside `extensionData`, and have the extension decode and check that field. The extension must verify the caller is a trusted router before trusting the embedded address.

2. **Extension-side:** Change the allowlist key from `sender` (the pool's `msg.sender`) to `recipient` when the sender is a known router, or require the extension to be configured with a set of trusted forwarders whose embedded user identity is accepted.

The simplest safe fix is to remove router support from allowlisted pools entirely and require direct pool calls, but this breaks the intended UX. The correct long-term fix is an authenticated user-identity forwarding mechanism analogous to ERC-2771 meta-transactions.

---

### Proof of Concept

```
Setup
─────
1. Deploy MetricOmmPool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, alice, true)       // alice is the intended user
3. Pool admin calls setAllowedToSwap(pool, router, true)      // to let alice use the router
4. LP adds liquidity to the pool.

Attack
──────
5. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: bob, ...})
   → router calls pool.swap(bob, ...)
   → pool passes msg.sender = router as `sender` to _beforeSwap
   → extension checks allowedSwapper[pool][router] == true  ✓
   → swap executes; Bob receives tokens from the pool.

Result
──────
Bob, who is not on the allowlist, successfully swaps against the curated pool.
The allowlist invariant is broken. Every user who calls the router can do the same.
``` [5](#0-4) [6](#0-5) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
