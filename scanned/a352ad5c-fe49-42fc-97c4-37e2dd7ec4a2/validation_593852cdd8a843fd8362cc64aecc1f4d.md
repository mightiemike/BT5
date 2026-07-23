### Title
`SwapAllowlistExtension` gates the router address instead of the actual swapper, enabling allowlist bypass via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which equals `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the router's address rather than the actual user. If the pool admin allowlists the router to enable router-mediated swaps, every unprivileged user can bypass the per-user allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension's caller) and `sender` is the address the pool received as its own `msg.sender` during `swap`. [1](#0-0) 

`MetricOmmPool.swap` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`: [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is `msg.sender` of the pool's `swap` call: [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. This produces two broken states:

1. **Router not allowlisted** — allowlisted users cannot use the supported periphery at all; their router-mediated swaps revert with `NotAllowedToSwap`.
2. **Router allowlisted** — every unprivileged user can bypass the per-user allowlist by routing through `MetricOmmSimpleRouter`, because the extension sees only the router address and approves it.

The `DepositAllowlistExtension` does not share this flaw — it correctly checks the `owner` parameter (the position owner), not `sender`: [5](#0-4) 

The inconsistency confirms that `SwapAllowlistExtension` is binding to the wrong actor.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` for curated access (KYC compliance, exclusive LP pools, institutional-only venues) is fully bypassed for any user who routes through `MetricOmmSimpleRouter`. If the admin allowlists the router — a natural step to let allowlisted users benefit from multi-hop routing — the allowlist is nullified for all router-mediated swaps. Unauthorized users trade against the pool's liquidity, violating the core access-control invariant and causing direct loss to LPs who deposited under the assumption of a curated counterparty set.

---

### Likelihood Explanation

The bypass requires the admin to allowlist the router address. This is a plausible and well-motivated configuration: an admin who wants allowlisted users to access multi-hop routing would add the router to the allowlist, not realizing it opens the gate to every user. The `generate_scanned_questions.py` audit brief explicitly flags this exact scenario as a primary target ("the hook must gate the same actor the pool designers thought they were allowlisting"). Likelihood is **medium** — the misconfiguration is easy to make and the exploit path is trivially reachable once it exists. [6](#0-5) 

---

### Recommendation

The extension must check the economically relevant actor — the user who initiated the swap — not the immediate `msg.sender` of the pool. Two viable approaches:

1. **Pass the originating user in `extensionData`**: the router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a trusted encoding convention between the router and the extension.
2. **Transient-storage originator**: the router writes the originating user into a well-known transient slot before calling the pool; the extension reads that slot. The pool's reentrancy guard already uses transient storage, so the pattern is established.

Either approach must be paired with a check that the `extensionData` originator field cannot be spoofed by a direct pool caller who bypasses the router.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, userA, true)   // allowlist userA
  admin calls setAllowedToSwap(pool, router, true)  // allowlist router to enable routing

Attack (userB, not allowlisted):
  userB → MetricOmmSimpleRouter.exactInputSingle({pool, ...})
    router → pool.swap(recipient, ...)          // msg.sender = router
      pool → _beforeSwap(sender=router, ...)
        extension: allowedSwapper[pool][router] == true  ✓
  swap executes for userB — allowlist bypassed

Victim (userA, allowlisted, tries to use router):
  userA → MetricOmmSimpleRouter.exactInputSingle({pool, ...})
    router → pool.swap(recipient, ...)          // msg.sender = router
      pool → _beforeSwap(sender=router, ...)
        extension: allowedSwapper[pool][router] == false (if router not allowlisted)
  revert NotAllowedToSwap — allowlisted user locked out of periphery
``` [1](#0-0) [7](#0-6) [2](#0-1) [8](#0-7)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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

**File:** generate_scanned_questions.py (L656-663)
```python
            short="swap allowlist gate",
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```
