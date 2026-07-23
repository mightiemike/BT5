### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Enabling Allowlist Bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which equals `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` intermediates the swap, `sender` is the router's address, not the actual user's address. The real user identity is never forwarded to the extension. This creates an irreconcilable bind: if the pool admin allowlists the router to let allowlisted users trade through it, every non-allowlisted user can also bypass the gate by routing through the same contract.

---

### Finding Description

**Extension check (wrong actor):** [1](#0-0) 

`beforeSwap` receives `sender` as its first argument and checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`.

**Pool passes `msg.sender` as `sender`:** [2](#0-1) 

When `MetricOmmSimpleRouter` calls `pool.swap()`, `msg.sender` inside the pool is the router, so `sender` forwarded to the extension is the router address.

**Router calls `pool.swap()` directly — original user address is never forwarded:** [3](#0-2) 

The original caller (`msg.sender` of `exactInputSingle`) is stored only in transient callback context for payment settlement. It is never passed to `pool.swap()` in a position the extension can read.

**Extension dispatch encodes `sender` = router:** [4](#0-3) 

`_callExtensionsInOrder` encodes `sender` (= router) into the call to `beforeSwap`. The actual end-user address is absent from the extension call entirely.

---

### Impact Explanation

The pool admin configures a `SwapAllowlistExtension` to restrict swaps to a curated set of addresses. Two outcomes are possible, both harmful:

1. **Router not allowlisted:** Allowlisted users cannot trade through `MetricOmmSimpleRouter` at all — the extension sees `sender` = router, which is not on the list, and reverts. Core swap functionality is broken for the supported periphery path.

2. **Router allowlisted (to fix case 1):** Any non-allowlisted user calls `router.exactInputSingle(pool, ...)`. The extension sees `sender` = router → `allowedSwapper[pool][router]` = `true` → swap executes. The entire allowlist is bypassed for every user who routes through the public router. This is a direct loss of the curation boundary the pool admin paid to enforce.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the documented, supported periphery path for end-user swaps. A pool admin who deploys a curated pool with `SwapAllowlistExtension` and wants allowlisted users to be able to use the router will naturally allowlist the router address. This is a foreseeable, non-malicious configuration step that silently opens the gate to all users. No privileged attacker capability is required beyond calling the public router.

---

### Recommendation

The extension must gate the **actual end-user**, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass the real user in `extensionData`:** The router encodes `msg.sender` into `extensionData` before calling `pool.swap()`. The `SwapAllowlistExtension` decodes and checks that address. The pool admin allowlists individual users, not the router.

2. **Check `sender` OR decode from `extensionData` with a trusted-router flag:** The extension checks `sender` for direct calls and decodes the real user from `extensionData` when `sender` is a known trusted router, verifying the router's identity via the factory's `isPool` or a separate registry.

Either approach ensures the allowlist always gates the economically relevant actor regardless of which supported entry point is used.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin.setAllowedToSwap(pool, alice, true)       // Alice is allowed
  admin.setAllowedToSwap(pool, router, true)      // admin adds router so Alice can use it

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  Execution trace:
    router.exactInputSingle()
      → pool.swap(recipient=bob, ...)          // msg.sender inside pool = router
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓ PASSES
        → swap executes, bob receives output tokens

Result:
  Bob, a non-allowlisted address, successfully swaps on a curated pool.
  The allowlist is fully bypassed.
``` [5](#0-4) [6](#0-5) [7](#0-6)

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
