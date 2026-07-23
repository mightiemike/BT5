### Title
`SwapAllowlistExtension.beforeSwap` Gates the Router Address Instead of the Actual Swapper, Enabling Allowlist Bypass - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension` is designed to restrict swaps on curated pools to a set of approved addresses. However, when a swap is routed through `MetricOmmSimpleRouter`, the extension receives the router's address as `sender` rather than the actual user's address. If the pool admin allowlists the router (a natural action to enable routing for approved users), any unprivileged user can bypass the individual allowlist by routing through the router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument — the router address when the swap originates from the router: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) calls `pool.swap(...)`, the pool's `msg.sender` is the router contract, not the end user: [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router]` — a single boolean that covers every user who routes through the router — rather than `allowedSwapper[pool][actual_user]`.

This creates an irreconcilable dilemma for the pool admin:

- **Router NOT allowlisted**: even individually-approved users cannot swap through the router (broken core functionality).
- **Router IS allowlisted**: every user, including those explicitly excluded from the allowlist, can bypass the gate by routing through the router.

There is no configuration that simultaneously permits approved users to use the router while blocking unapproved ones.

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC-verified addresses, protocol-owned addresses, or specific market makers) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. An unapproved user can execute arbitrary swaps against the pool's liquidity, potentially extracting value from LPs whose strategy assumed only trusted counterparties would trade.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical public swap interface. A pool admin who deploys a curated pool and wants approved users to be able to use the router will naturally allowlist the router address. The bypass is then immediately reachable by any unprivileged user with no further preconditions. The project's own audit target description explicitly flags this path: *"the hook must gate the same actor the pool designers thought they were allowlisting"* and *"assert the hook cannot be bypassed by routing through an intermediate public contract."* [5](#0-4) 

### Recommendation

The extension must identify the true economic actor, not the intermediary. Two approaches:

1. **Pass the real user through `extensionData`**: have the router encode `msg.sender` into `extensionData` and have the extension decode and verify it. This requires a coordinated convention between the router and the extension.
2. **Check `sender` only for direct pool calls; require the router to forward the user address**: add a dedicated field to the swap parameters or extension data that carries the originating user address, and have `SwapAllowlistExtension` read from that field when present.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   // alice is approved
  allowedSwapper[pool][router] = true  // router allowlisted so alice can route
  allowedSwapper[pool][bob]   = false  // bob is NOT approved

Attack:
  bob calls router.exactInputSingle({ pool: pool, ... })
  → router calls pool.swap(recipient, ...) with msg.sender = router
  → pool calls _beforeSwap(sender=router, ...)
  → SwapAllowlistExtension checks allowedSwapper[pool][router] == true → PASSES
  → bob's swap executes on the restricted pool
```

Bob, who is explicitly excluded from the allowlist, successfully trades against the pool's liquidity because the extension evaluated the router's allowlist entry rather than his own.

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
