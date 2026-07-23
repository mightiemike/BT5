### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, allowing any unprivileged caller to bypass the swap allowlist on curated pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is `msg.sender` of `pool.swap()`. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router contract**, not the end user. The allowlist check therefore gates the router address, not the actual swapper. If the pool admin allowlists the router (the natural step to enable router-based swaps for their curated users), every unprivileged user can bypass the allowlist by routing through the router.

---

### Finding Description

**Call chain:**

```
User EOA
  → MetricOmmSimpleRouter.exactInputSingle(...)
      → pool.swap(recipient, zeroForOne, ..., extensionData)   // msg.sender = router
          → _beforeSwap(msg.sender=router, ...)                // MetricOmmPool.sol:230-240
              → ExtensionCalling._beforeSwap(sender=router, ...)
                  → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                      → allowedSwapper[pool][router]           // checked, NOT the EOA
```

In `MetricOmmPool.swap`, `msg.sender` (the immediate caller) is forwarded as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that same `sender` value and dispatches it to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is whoever called `pool.swap()` — the router, not the end user: [3](#0-2) 

The router calls `pool.swap` directly with no mechanism to forward the original EOA identity: [4](#0-3) 

**Two broken states arise:**

| Pool admin intent | Result |
|---|---|
| Allowlist specific EOAs; also allowlist the router so those EOAs can use the router | Router is allowlisted → **every** user passes the check; allowlist is fully bypassed |
| Allowlist specific EOAs only (no router) | Allowlisted EOAs cannot use the router at all; the router is blocked |

Neither state matches the intended policy: "only allowlisted users may swap, via any supported path."

---

### Impact Explanation

**High.** A curated pool using `SwapAllowlistExtension` is designed to restrict swaps to a known set of counterparties (e.g., institutional LPs, KYC'd users, or protocol-controlled addresses). Once the router is allowlisted — the necessary step to let any allowlisted user trade through the standard periphery — the guard fails open for all callers. Any unprivileged user can drain LP-owned token balances at oracle-quoted prices, causing direct loss of LP principal. The pool's core swap functionality is broken relative to its declared security invariant.

---

### Likelihood Explanation

**Medium.** The pool admin must allowlist the router to enable router-based swaps for their intended users. This is the expected operational step; the `MetricOmmSimpleRouter` is the canonical periphery entry point. Any pool that deploys `SwapAllowlistExtension` and also wants to support the router will trigger this condition. The attacker needs no special privilege — a single `exactInputSingle` call through the router suffices.

---

### Recommendation

Pass the original EOA through the swap path so the extension can gate the economically relevant actor. Two options:

1. **Preferred — pass `recipient` or an explicit `originator` field**: Add an `originator` parameter to `pool.swap` that the router populates with `msg.sender` before calling the pool. The pool forwards it as the first argument to `_beforeSwap`. The extension checks `allowedSwapper[pool][originator]`.

2. **Minimal fix — check `recipient` instead of `sender`**: For single-hop swaps the `recipient` is the user's address. However, for multi-hop swaps the recipient of intermediate hops is the router itself, so this is not a general fix.

The cleanest invariant: the allowlist must gate the address that **economically benefits** from the swap (the `recipient` for single-hop, or the originating EOA for multi-hop), not the address that mechanically calls `pool.swap`.

---

### Proof of Concept

```solidity
// Pool admin sets up a curated pool
swapExtension.setAllowedToSwap(address(pool), address(router), true); // allowlist router
// (admin believes only allowlisted EOAs will trade; they allowlist the router to support periphery)

// Attacker — NOT in the allowlist — calls through the router
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: attacker,
    zeroForOne: true,
    amountIn: 1_000e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    tokenIn: token0,
    extensionData: ""
}));
// pool.swap(msg.sender=router) → _beforeSwap(sender=router)
// allowedSwapper[pool][router] == true → passes
// Attacker receives token1 at oracle price; allowlist is bypassed
``` [5](#0-4) [6](#0-5) [1](#0-0)

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
