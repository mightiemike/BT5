### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass the Curated-Pool Allowlist - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is `msg.sender` of the `pool.swap()` call. When a user swaps through `MetricOmmSimpleRouter`, the router is `msg.sender` in the pool, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. A disallowed user can bypass the allowlist on any curated pool by routing through the standard periphery router.

---

### Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is in the per-pool allowlist: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly: [4](#0-3) 

At that point `msg.sender` inside the pool is the **router contract**, not the originating user. The extension therefore evaluates `allowedSwapper[pool][router]` — a check that is entirely independent of which user initiated the transaction.

The same misbinding occurs for every router entry point (`exactInput`, `exactOutputSingle`, `exactOutput`), and for intermediate hops in multi-hop paths where `address(this)` (the router itself) is the caller: [5](#0-4) 

---

### Impact Explanation

There are two failure modes, both fund-impacting:

1. **Allowlist bypass**: A pool admin allowlists the router so that legitimate users can trade through the standard periphery path. Because the extension cannot distinguish callers behind the router, every user — including explicitly disallowed ones — can now swap on the curated pool. Disallowed users gain full access to pool liquidity at oracle-anchored prices, directly violating the curation policy and potentially draining LP value through adversarial trading.

2. **Legitimate users blocked**: If the pool admin does *not* allowlist the router (trying to gate by individual address), every allowlisted user who goes through the router is rejected even though they are permitted. Core swap functionality is broken for the standard periphery path.

Both outcomes represent broken core pool functionality or direct loss of LP assets above contest thresholds.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the production periphery entry point for swaps. Any user who calls `exactInputSingle` or `exactInput` through it triggers the misbinding automatically, with no special setup. The bypass requires no privileged access, no malicious token, and no unusual pool configuration — only the existence of a `SwapAllowlistExtension` on the pool and the router being the caller.

---

### Recommendation

The extension must gate the **economic principal**, not the immediate caller of `pool.swap`. Two sound approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated convention between router and extension.

2. **Check `tx.origin` as a fallback** (only if the pool admin explicitly opts in and understands the implications): not generally recommended.

3. **Preferred — redesign the allowlist to key on the payer stored in transient context**: The router already stores the real payer in transient storage (`_getPayer()`). Expose that via a standard interface so the extension can read the true originator.

The simplest safe fix is option 1: the router appends `abi.encode(msg.sender)` to `extensionData` before forwarding to the pool, and the extension decodes it when `msg.sender` (the pool) is a known pool from the factory.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (necessary so legitimate users can trade via the router).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  1. attacker calls MetricOmmSimpleRouter.exactInputSingle({
       pool: curated_pool,
       recipient: attacker,
       ...
     });
  2. Router calls pool.swap(recipient=attacker, ...) — msg.sender = router.
  3. Pool calls _beforeSwap(sender=router, ...).
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true.
  5. Swap executes. Attacker receives output tokens from the curated pool.

Result: attacker, who was never allowlisted, successfully swaps on the
curated pool. The allowlist guard is completely bypassed.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```
