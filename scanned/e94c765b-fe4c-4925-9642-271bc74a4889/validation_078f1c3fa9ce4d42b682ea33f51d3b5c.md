### Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Enabling Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded by the pool. The pool always passes `msg.sender` as `sender`, which is the **router address** when a user enters through `MetricOmmSimpleRouter`. Because the extension checks the router's address against the per-pool allowlist rather than the original user's address, any user can bypass a curated swap allowlist by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool forwarded: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(...)` directly — no original-user address is threaded through: [4](#0-3) 

The pool therefore sees `msg.sender = router`, and the extension checks `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`.

**Two exploitable outcomes arise:**

1. **Allowlist bypass (High):** If the pool admin allowlists the router address (the natural step to let users reach the pool via the supported periphery), every unprivileged user can swap on the restricted pool by routing through `MetricOmmSimpleRouter`. The allowlist is completely defeated.

2. **Broken legitimate access:** If the pool admin allowlists individual user addresses, those users cannot swap through the router at all — the extension reverts because the router is not on the list. Core swap functionality is broken for the intended audience.

The analog to the external report is direct: just as the `EnforcedTxGateway` verified a queue index that was fetched at submission time rather than at signing time — causing a mismatch between the intended value and the checked value — `SwapAllowlistExtension` verifies the identity of the immediate pool caller (the router) rather than the identity of the economic actor the pool admin intended to gate (the original user). The intermediary (router / transaction submitter) introduces the mismatch.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to KYC'd or otherwise curated addresses can be fully bypassed by any unprivileged user routing through `MetricOmmSimpleRouter`. The attacker pays no special cost beyond normal gas. All swap volume and associated fee extraction on the restricted pool becomes accessible to unauthorized parties, directly violating the pool's curation invariant and potentially causing loss of LP principal if the pool was designed to trade only with trusted counterparties.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary supported periphery entry point for end users. Any user who discovers the allowlist can trivially route through the router. The router is a public, permissionless contract. No privileged access, malicious setup, or non-standard token behavior is required.

---

### Recommendation

The pool must pass the **original initiating user** to extensions, not `msg.sender`. Two viable approaches:

1. **Add an explicit `swapper` parameter to `pool.swap`** that callers supply and the pool forwards to extensions. The router would pass `msg.sender` (the original user) as `swapper`. The pool enforces that `swapper` is either `msg.sender` or an address the caller is authorized to act for.

2. **Check `tx.origin` in the extension** as a fallback identity. This is weaker (breaks contract-wallet flows) but closes the router bypass without a pool interface change.

The `DepositAllowlistExtension` has the same structural issue for the `MetricOmmPoolLiquidityAdder` path and should be reviewed in parallel. [5](#0-4) 

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, address(router), true)
    (to allow router-mediated swaps for allowlisted users).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  1. Attacker (not on allowlist) calls:
       router.exactInputSingle({pool: pool, ..., recipient: attacker, ...})
  2. Router calls pool.swap(attacker, ...) — pool sees msg.sender = router.
  3. Pool calls _beforeSwap(router, attacker, ...).
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true.
  5. Swap executes. Attacker receives output tokens.

Result:
  Attacker bypassed the swap allowlist entirely. Any user can repeat this.
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

**File:** metric-core/contracts/ExtensionCalling.sol (L151-177)
```text
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
