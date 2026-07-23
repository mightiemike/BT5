### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Complete Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against a per-pool allowlist. When a user routes through `MetricOmmSimpleRouter`, the `sender` the extension receives is the **router's address**, not the actual user. If the pool admin allowlists the router (the only way to let allowlisted users reach the pool through the standard periphery), every non-allowlisted user can bypass the curated-pool gate by routing through the same router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks that forwarded `sender` against the per-pool allowlist: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`) is the entry point, the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` inside the pool is the **router contract**, so the extension evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][actual_user]`. There is no mechanism in the router or the extension protocol to thread the original EOA through to the hook.

This creates two mutually exclusive failure modes:

| Admin choice | Outcome |
|---|---|
| Allowlist the router address | Every user on the internet can swap through the router; the curated-pool invariant is completely broken |
| Do not allowlist the router | Individually allowlisted users cannot use the standard periphery; they must call `pool.swap()` directly and implement `IMetricOmmSwapCallback` themselves |

Neither option lets the pool admin enforce a per-user allowlist while still supporting the standard router.

---

### Impact Explanation

A curated pool (e.g., KYC-only, institution-only, or protocol-internal) that deploys `SwapAllowlistExtension` and allowlists the router loses all access control over who can trade. Any non-allowlisted address can execute swaps, draining LP value at oracle-derived prices. This is a **direct loss of LP principal** and a **broken core pool functionality** impact: the pool's stated access policy is silently voided for every router-mediated swap.

---

### Likelihood Explanation

The router is the canonical, documented entry point for end-users. A pool admin who wants allowlisted users to be able to use the standard UI/SDK will inevitably allowlist the router. The bypass requires no special privilege, no flash loan, and no multi-step setup — a single `exactInputSingle` call from any EOA suffices. Likelihood is **high**.

---

### Recommendation

The extension must be able to identify the true economic actor, not the intermediary contract. Two viable approaches:

1. **Pass the originating user in `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool; the extension decodes and verifies it. This requires a convention between router and extension but keeps the core unchanged.

2. **Check `sender` only when it is not a known periphery contract, and require the router to forward the real user**: Add a `trustedForwarder` registry to the extension so that when `sender == router`, the extension reads the actual user from a router-supplied field in `extensionData`.

Either way, the extension must never treat a shared intermediary contract as the identity to gate.

---

### Proof of Concept

```
Setup
─────
1. Deploy pool with SwapAllowlistExtension as extension1.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — necessary so that allowlisted users can reach the pool via the router.
3. Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack
──────
4. Attacker (non-allowlisted EOA) calls:
       router.exactInputSingle({pool: pool, ..., extensionData: ""})

5. Router calls pool.swap(recipient, zeroForOne, amount, limit, "", "").
   pool.msg.sender = router.

6. Pool calls _beforeSwap(sender=router, ...).

7. Extension evaluates:
       allowAllSwappers[pool]          → false
       allowedSwapper[pool][router]    → true   ← set in step 2

8. Extension returns selector — no revert.

9. Swap executes at oracle price. Attacker receives output tokens.
   Allowlist is completely bypassed.
``` [5](#0-4) [6](#0-5)

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
