### Title
`SwapAllowlistExtension` checks router address instead of end-user address, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument passed from the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `swap()` passes `msg.sender` (the router contract) as `sender` to the extension. The extension therefore checks whether the **router** is allowlisted, not whether the **end user** is allowlisted. A pool admin who allowlists the router to support router-mediated swaps inadvertently grants every user on-chain access to the pool, completely defeating the per-user allowlist.

---

### Finding Description

**Call path:**

```
user → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ..., extensionData)   [msg.sender = router]
              → ExtensionCalling._beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  ← checked, NOT the end user
```

In `MetricOmmPool.swap()`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()` — the router, not the end user: [3](#0-2) 

The router stores the real payer in transient storage for the payment callback, but that value is never surfaced to the extension: [4](#0-3) 

**The two broken states this creates:**

| Admin configuration | Result |
|---|---|
| Allowlist specific users (not the router) | Allowlisted users **cannot** swap through the router; they must call the pool directly. Router-mediated flow is broken for legitimate users. |
| Allowlist the router (to support router flow) | **Every** user can bypass the per-user allowlist by routing through the public router. |

Neither configuration achieves the intended goal of restricting swaps to specific users while supporting the router.

---

### Impact Explanation

**Direct loss / broken core functionality.** A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers) can be fully bypassed by any unprivileged user calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The attacker receives the pool's output tokens at oracle-anchored prices, draining LP value that was intended to be accessible only to allowlisted parties. This matches the "allowlist bypass" and "wrong-actor binding" impact categories.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary public entrypoint for swaps; most users are expected to route through it.
- No special privilege is required — any EOA can call `exactInputSingle`.
- The bypass is deterministic and requires no timing, oracle manipulation, or front-running.
- Any pool that deploys `SwapAllowlistExtension` and also expects users to use the router is immediately affected.

---

### Recommendation

The extension must receive and check the **original end-user identity**, not the intermediary router. Two complementary fixes:

1. **Pass the original initiator through the router.** The router already stores `msg.sender` (the real user) in transient storage as the payer. It should also forward it as part of `extensionData` or as a dedicated field so the extension can verify it.

2. **Check `sender` against the real user in the extension.** Alternatively, the pool could pass the original initiator as a separate parameter, or the extension could require the router to attest the real user inside `extensionData` (with the router's address as the trusted attestor).

A minimal fix in the extension alone is not sufficient because the extension only sees what the pool passes. The fix must originate at the router layer, ensuring the real user identity reaches the extension before the allowlist check executes.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `alice` is allowlisted.
swapExtension.setAllowedToSwap(address(pool), alice, true);
// Admin also allowlists the router so alice can use it:
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Attack: charlie (not allowlisted) routes through the router.
vm.prank(charlie);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    tokenIn: token0,
    recipient: charlie,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// Extension checks allowedSwapper[pool][router] == true → passes.
// Charlie receives token1 output despite not being allowlisted.
```

The root cause is that `SwapAllowlistExtension.beforeSwap` receives `sender = address(router)` instead of `sender = charlie`, mirroring the external bug's pattern of the wrong identity/mode being bound at the critical check point. [5](#0-4) [6](#0-5)

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
