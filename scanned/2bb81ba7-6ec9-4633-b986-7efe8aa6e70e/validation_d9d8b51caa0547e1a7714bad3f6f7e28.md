### Title
`SwapAllowlistExtension` checks the router address instead of the end user, allowing any unprivileged caller to bypass a curated pool's swap allowlist via `MetricOmmSimpleRouter` - (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool. When a user swaps through `MetricOmmSimpleRouter`, the pool's `swap()` is called by the router, so `msg.sender` inside the pool is the router address. The pool forwards `msg.sender` (the router) as `sender` to the extension hook. The extension therefore checks `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actual_user]`. If the pool admin allowlists the router to enable standard periphery access for their users, every unprivileged address can bypass the allowlist by routing through the router.

---

### Finding Description

**Root cause — wrong actor binding in `SwapAllowlistExtension.beforeSwap`:**

```
MetricOmmSimpleRouter.exactInputSingle()
  → pool.swap(recipient, ..., extensionData)   // msg.sender = router
      → _beforeSwap(msg.sender=router, ...)    // MetricOmmPool.sol:231
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
              → allowedSwapper[pool][router]   // checks router, not user
``` [1](#0-0) 

The pool passes `msg.sender` (the direct caller of `pool.swap()`) as `sender` to every extension hook. [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap()` — the router, not the end user. [3](#0-2) 

The router calls `pool.swap()` directly with no mechanism to forward the original `msg.sender` to the extension layer. [4](#0-3) 

**The two broken outcomes:**

1. **Allowlist bypass (High):** The pool admin allowlists the router address so that their allowlisted users can use the standard periphery. Because `allowedSwapper[pool][router] = true`, the check passes for *any* caller of the router — including addresses that were never individually allowlisted. Any unprivileged user can call `exactInputSingle` on the router and swap on the curated pool.

2. **Broken core functionality:** If the pool admin does *not* allowlist the router, individually allowlisted users cannot use `MetricOmmSimpleRouter` at all — their swaps revert with `NotAllowedToSwap` because the router address is not in the allowlist. The standard periphery path is unusable for curated pools.

There is no third option: the extension design forces the pool admin to choose between a broken allowlist or a broken periphery.

---

### Impact Explanation

**Direct impact:** Unprivileged users can execute swaps on pools explicitly configured to restrict access (e.g., KYC-gated, institutional, or protocol-internal pools). The allowlist — the sole access-control mechanism on the swap path — is fully bypassed. Every swap that executes on a restricted pool moves real token balances; the attacker receives output tokens they are not entitled to, and the pool's LP positions absorb the price impact intended only for authorized counterparties.

**Severity:** High. The broken invariant is the allowlist itself. The bypass is unconditional once the router is allowlisted, requires no special privileges, and is reachable through the documented public periphery path.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary user-facing swap interface; pool admins who want their allowlisted users to use the standard periphery *must* allowlist the router.
- The bypass requires only a standard `exactInputSingle` call — no flash loans, no multi-step setup, no privileged access.
- Any actor who observes that a pool has `SwapAllowlistExtension` configured and the router is allowlisted can immediately exploit it.

---

### Recommendation

The extension must check the *economic actor* — the end user — not the intermediary contract. Two complementary fixes:

1. **Pass the original user through the router:** The router should forward the original `msg.sender` in `extensionData` (or a dedicated field), and the extension should decode and verify it. This requires a coordinated change to the router and extension.

2. **Check `recipient` instead of `sender` for swap allowlisting:** If the intent is to gate who *receives* output tokens, `recipient` is the correct field. If the intent is to gate who *initiates* the swap, the router must propagate the initiator's identity.

3. **Document the incompatibility:** At minimum, add an explicit warning that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter` for per-user allowlisting, and that allowlisting the router address opens the gate to all callers.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowlisted
  - Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use periphery

Attack (executed by bob, who is NOT allowlisted):
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({
       pool: pool,
       recipient: bob,
       zeroForOne: true,
       amountIn: X,
       ...
     })
  2. Router calls pool.swap(bob, true, X, ...) — msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true ✓
  5. Swap executes; bob receives output tokens.

Expected: revert NotAllowedToSwap (bob is not allowlisted)
Actual:   swap succeeds — allowlist fully bypassed
``` [3](#0-2) [1](#0-0) [5](#0-4)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
