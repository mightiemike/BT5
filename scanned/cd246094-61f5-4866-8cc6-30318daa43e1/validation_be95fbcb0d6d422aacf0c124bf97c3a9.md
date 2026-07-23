### Title
`SwapAllowlistExtension` checks router address instead of end-user identity, allowing full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension sees the router's address — not the actual end user. A pool admin who allowlists the router to enable router-mediated swaps inadvertently grants every user on-chain access to the pool, completely defeating the allowlist.

---

### Finding Description

**Call path:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ..., extensionData)   // msg.sender = router
              → _beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  // checks router, not user
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to every before-swap hook: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` the pool sees: [4](#0-3) 

This creates an irreconcilable dilemma for any pool admin who deploys a curated pool with `SwapAllowlistExtension`:

| Pool admin action | Result |
|---|---|
| Allowlist individual users only (not the router) | Allowlisted users **cannot** use the router; router calls revert `NotAllowedToSwap` |
| Allowlist the router address | **Every** user can bypass the allowlist by routing through `MetricOmmSimpleRouter` |

There is no configuration that simultaneously enforces per-user allowlist policy **and** supports router-mediated swaps.

The protocol's own security analysis in `generate_scanned_questions.py` identifies the invariant that must hold: [5](#0-4) 

The current implementation breaks this invariant.

---

### Impact Explanation

A pool configured as a curated/private venue (e.g., KYC-gated, institution-only, or restricted-counterparty) relies on `SwapAllowlistExtension` to enforce access. If the pool admin allowlists the router to give their approved users a better UX, any unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle` targeting that pool and execute swaps. Unauthorized swaps against a pool designed for specific counterparties can drain LP principal through adverse selection, front-running, or sandwich attacks that the allowlist was intended to prevent. This is a direct loss of LP assets — High severity.

---

### Likelihood Explanation

Any pool that deploys `SwapAllowlistExtension` and also wants users to interact via `MetricOmmSimpleRouter` (the standard periphery router) will face this issue. The pool admin allowlisting the router is a natural, good-faith operational step. The bypass is then reachable by any unprivileged user with no special knowledge beyond the router's public address. Likelihood is **High** for pools that combine both components.

---

### Recommendation

The extension must gate the actual end user, not the intermediate router. Two approaches:

1. **Pass the real user in `extensionData`**: The router encodes `msg.sender` (the actual user) into `extensionData` before forwarding to the pool. The extension decodes and checks that address. This requires a convention between the router and the extension.

2. **Dedicated router wrapper**: The router exposes a `swapOnBehalfOf(address user, ...)` entry point and the extension is updated to decode the real user from a standardized `extensionData` field, verifying the router is the caller.

Either approach must ensure the router cannot be spoofed by a non-router caller supplying a fake user address in `extensionData`.

---

### Proof of Concept

```solidity
// Pool admin sets up a curated pool:
swapExtension.setAllowedToSwap(pool, router, true);  // allowlist the router so approved users can use it
// (approved users are NOT individually allowlisted — admin expects router to carry their identity)

// Unauthorized user bypasses the allowlist:
router.exactInputSingle(ExactInputSingleParams({
    pool: curatedPool,
    recipient: attacker,
    zeroForOne: true,
    amountIn: largeAmount,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    tokenIn: token0,
    extensionData: "",
    deadline: block.timestamp
}));
// → pool.swap(msg.sender=router) → beforeSwap(sender=router) → allowedSwapper[pool][router] == true → PASSES
// Unauthorized swap executes against LP funds.
```

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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

**File:** generate_scanned_questions.py (L733-738)
```python
            title="allowlist bypass",
            question_focus="a curated pool's allowlist can be bypassed through a public router or liquidity-adder path",
            exploit="Enter through the supported periphery path rather than the direct pool call and see whether the identity check changes.",
            invariant="A curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it.",
            impact="High direct loss or curation failure if disallowed users can still trade or deposit.",
        ),
```
