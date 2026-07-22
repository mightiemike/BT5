### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is the `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender = router`. The allowlist therefore gates the router address, not the actual end user. This produces two fund-impacting failure modes: (1) allowlisted users cannot use the supported router periphery path at all, and (2) if the pool admin allowlists the router to restore router access, every unprivileged user bypasses the allowlist entirely.

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct) and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`, which is the `msg.sender` of the originating `pool.swap()` call. [2](#0-1) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any router entry point) calls `pool.swap()`, the router contract is `msg.sender`, so `sender = router`: [3](#0-2) 

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`. The allowlist is configured for individual users but is applied against the intermediary contract — an exact analog to the LivenessModule bug where `THRESHOLD_PERCENTAGE` is applied against the wrong reference value because two sources of truth diverge.

The contest's own validation focus for this target states: *"Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."* [4](#0-3) 

### Impact Explanation

**Mode 1 — Broken swap flow (no admin action required):** Any allowlisted user who calls `router.exactInputSingle` or `router.exactInput` on a curated pool is rejected because the router is not in the allowlist. The supported periphery path is completely unusable for curated pools, breaking the intended swap flow.

**Mode 2 — Full allowlist bypass (one semi-trusted admin action):** A pool admin who wants to restore router access for their allowlisted users will naturally call `setAllowedToSwap(pool, router, true)`. Because the router has no access control of its own — any address can call `exactInputSingle` — this single action opens the pool to every unprivileged user. The allowlist is defeated entirely, allowing unauthorized swaps that drain LP value or violate compliance requirements.

### Likelihood Explanation

The router is the primary supported periphery path. Pool admins operating curated pools will encounter Mode 1 immediately when their allowlisted users try to use the router. The natural remediation (allowlisting the router) triggers Mode 2. No oracle manipulation, no flash loan, and no malicious setup is required — only normal usage of the supported router.

### Recommendation

The extension must check the original end user, not the direct pool caller. Two viable approaches:

1. **Pass originator through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. Requires a router-aware extension variant.
2. **Add an `originator` field to the extension interface:** The pool passes both `sender` (direct caller) and an `originator` (the address that initiated the outermost call) so extensions can gate the economically relevant actor.

### Proof of Concept

**Mode 1 — allowlisted user blocked by router:**

```
1. Deploy pool with SwapAllowlistExtension.
2. setAllowedToSwap(pool, alice, true)          // alice is allowlisted
3. alice calls router.exactInputSingle(pool, …)
4. router calls pool.swap(); msg.sender = router
5. extension checks allowedSwapper[pool][router] → false → NotAllowedToSwap
6. alice's swap reverts despite being allowlisted
```

**Mode 2 — unprivileged bypass after admin fixes Mode 1:**

```
1. setAllowedToSwap(pool, router, true)         // admin allowlists router to fix Mode 1
2. bob (not allowlisted) calls router.exactInputSingle(pool, …)
3. router calls pool.swap(); msg.sender = router
4. extension checks allowedSwapper[pool][router] → true → passes
5. bob swaps successfully on a pool he was never authorized to access
``` [5](#0-4) [6](#0-5) [2](#0-1)

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

**File:** generate_scanned_questions.py (L655-663)
```python
        Target(
            short="swap allowlist gate",
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```
