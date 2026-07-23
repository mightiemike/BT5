### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, allowing any caller to bypass the per-pool swap allowlist through the router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the immediate `msg.sender` of `pool.swap()`. When a swap is routed through `MetricOmmSimpleRouter`, that `sender` is the **router contract**, not the end user. A pool admin who allowlists the router to enable router-mediated swaps for their intended users inadvertently opens the pool to **every** user who calls the router, completely defeating the allowlist.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument — the router address when the swap originates from `MetricOmmSimpleRouter`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly with no user-identity forwarding: [4](#0-3) 

The allowlist is therefore keyed on `(pool → router)`, not `(pool → actual user)`. A pool admin faces an impossible choice:

- **Do not allowlist the router** → every legitimate allowlisted user is blocked from using the router; they must call `pool.swap` directly.
- **Allowlist the router** → the check `allowedSwapper[pool][router] == true` passes for every caller regardless of their individual allowlist status, making the guard a no-op for all router-mediated swaps.

---

### Impact Explanation

Any user who is explicitly **not** on the allowlist can bypass the curated-pool restriction by routing through `MetricOmmSimpleRouter`. The allowlist is the sole on-chain mechanism the pool admin has to restrict swap access. Once bypassed, disallowed counterparties can trade against the pool's LP positions under conditions the admin never intended to permit, exposing LPs to adversarial flow and direct principal loss. This is an admin-boundary break: an unprivileged path (`MetricOmmSimpleRouter`) nullifies a pool-admin-configured access control.

---

### Likelihood Explanation

High. The router is the canonical user-facing entry point documented and deployed by the protocol. Any pool admin who wants their allowlisted users to have a normal UX must allowlist the router, at which point the bypass is unconditional and requires no special setup from the attacker — a single `exactInputSingle` call suffices.

---

### Recommendation

The extension must gate the **economically relevant actor** — the end user — not the intermediary contract. Two viable approaches:

1. **Pass the real user through `extensionData`**: the router encodes `msg.sender` into `extensionData` and the extension decodes and checks it. This requires a trusted router assumption.
2. **Check `sender` against a router registry and fall back to a user-supplied identity**: the extension recognises known routers and reads the actual user from a standardised field in `extensionData`.

The `DepositAllowlistExtension` already demonstrates the correct pattern — it ignores `sender` and checks `owner` (the economically relevant party): [5](#0-4) 

`SwapAllowlistExtension` should adopt an equivalent design.

---

### Proof of Concept

```
Setup
─────
1. Pool P is deployed with SwapAllowlistExtension E configured.
2. Pool admin calls E.setAllowedToSwap(P, alice, true)   // alice is the intended trader
3. Pool admin calls E.setAllowedToSwap(P, router, true)  // needed so alice can use the router

Attack
──────
4. bob (not allowlisted) calls:
       router.exactInputSingle({pool: P, recipient: bob, ...})

5. Router executes:
       P.swap(bob, zeroForOne, amount, priceLimit, "", extensionData)
       // msg.sender inside pool.swap == router

6. Pool calls:
       E.beforeSwap(router, bob, ...)
       // msg.sender inside extension == P
       // sender argument == router

7. Extension evaluates:
       allowAllSwappers[P]          → false
       allowedSwapper[P][router]    → true   ← passes!

8. Swap executes. bob trades on the curated pool despite never being allowlisted.
``` [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
