### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so the extension checks whether the **router** is allowlisted rather than the actual user. If the router is allowlisted (which is required for router-mediated swaps to function on a curated pool), any unpermissioned user can bypass the allowlist entirely by routing through the public router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension dispatcher.**

In `MetricOmmPool.swap`, the pool calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

**Step 2 — `ExtensionCalling._beforeSwap` forwards that value unchanged as `sender`.** [2](#0-1) 

**Step 3 — `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`.**

`msg.sender` here is the pool (correct), and `sender` is the first argument — which is the router address when the call originates from `MetricOmmSimpleRouter`: [3](#0-2) 

**Step 4 — `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making itself `msg.sender` at the pool.** [4](#0-3) 

The router never forwards the original `msg.sender` (the actual user) to the pool. The pool therefore passes the router's address as `sender` to the extension. The extension then evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][actualUser]`.

This creates an irreconcilable dilemma for any pool admin who configures `SwapAllowlistExtension`:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all — core periphery functionality broken |
| **Allowlist the router** | Any user, allowlisted or not, can bypass the gate by routing through the public router |

There is no configuration that simultaneously enforces the allowlist and permits router-mediated swaps.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd users, protocol-owned addresses, or whitelisted market makers) is completely unprotected against any public user who routes through `MetricOmmSimpleRouter`. The allowlist guard silently passes for the router address, allowing unauthorized swaps to execute and drain LP-owned assets at oracle-derived prices. This is a direct loss of LP principal and a broken core pool invariant.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary public swap entrypoint documented and expected for end-user interaction. Any user who discovers the bypass can exploit it immediately with no privileged access, no special setup, and no malicious token behavior. The only precondition is that the pool admin has allowlisted the router (a natural and necessary step to make the pool usable via the standard periphery).

---

### Recommendation

The pool should receive the original user's address rather than the router's address. Two approaches:

1. **Router forwards the original caller**: `MetricOmmSimpleRouter` encodes `msg.sender` into `extensionData` and the extension decodes it, verifying that `msg.sender` (the pool) is a known factory pool before trusting the payload.
2. **Extension reads from `extensionData`**: Define a standard envelope where the router always prepends the original caller's address to `extensionData`, and `SwapAllowlistExtension` decodes and checks that address instead of the raw `sender` argument.

Either approach must be authenticated (the extension must verify the pool is legitimate before trusting the embedded address) to prevent spoofing.

---

### Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension
  admin calls extension.setAllowedToSwap(pool, router, true)   // necessary for router to work
  admin calls extension.setAllowedToSwap(pool, alice, true)    // intended allowlisted user
  bob = arbitrary non-allowlisted address

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({
    pool: pool,
    recipient: bob,
    zeroForOne: true,
    amountIn: X,
    ...
  })

  → Router calls pool.swap(bob, true, X, ...) with msg.sender = router
  → Pool calls _beforeSwap(sender=router, ...)
  → Extension checks allowedSwapper[pool][router] → true (router was allowlisted)
  → Swap executes; bob receives output tokens
  → Allowlist completely bypassed
```

Bob, who is not on the allowlist, successfully swaps on the curated pool. The allowlist extension never evaluated `allowedSwapper[pool][bob]`.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
