### Title
`SwapAllowlistExtension` gates on router address instead of end user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the pool's own `swap()` call. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router address (a natural action to enable router usage), every user — including those explicitly excluded from the allowlist — can bypass the curation gate by routing through the router.

### Finding Description

The pool's `swap()` function passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards it verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap()` directly, making the router the `msg.sender` seen by the pool: [4](#0-3) 

The end user's identity (`msg.sender` of the router call) is stored only in transient callback context for payment settlement and is never forwarded to the pool or the extension. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][endUser]`.

**Contrast with `DepositAllowlistExtension`**: the deposit guard correctly ignores the `sender` (first parameter, the liquidity adder) and checks `owner` (the position owner), so it identifies the correct economic actor regardless of which periphery contract calls `addLiquidity`: [5](#0-4) 

The swap allowlist has no equivalent `owner`-style parameter — the swap interface carries no explicit "swapper" field — so the extension is structurally bound to the wrong actor when a router intermediary is present.

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and then calls `setAllowedToSwap(pool, router, true)` — a natural action to let users trade through the supported periphery — inadvertently grants every address on-chain the ability to bypass the allowlist. Any non-allowlisted user routes through `MetricOmmSimpleRouter`; the extension sees `sender = router`, finds it allowlisted, and permits the swap. The curation policy is completely voided: LP funds in the curated pool are exposed to unauthorized counterparties, defeating the pool's intended access control and potentially causing direct LP losses if the allowlist was protecting against adversarial or uninformed traders.

### Likelihood Explanation

The trigger is a pool admin calling `setAllowedToSwap(pool, router, true)`. This is a semi-trusted, non-malicious action that any operator of a curated pool would reasonably take to enable the standard periphery flow. The admin has no indication from the interface or documentation that allowlisting the router is semantically equivalent to disabling the allowlist for all users. Once the router is allowlisted, the bypass is available to any unprivileged address with zero additional preconditions.

### Recommendation

The `SwapAllowlistExtension` must identify the true economic actor, not the intermediary. Two viable approaches:

1. **Explicit swapper field in `extensionData`**: require the router to encode the end user's address in `extensionData`; the extension decodes and checks it. The router already has `msg.sender` available at entry.
2. **Separate allowlist entry for routers with per-user forwarding**: add a trusted-router registry; when `sender` is a trusted router, decode the real swapper from `extensionData` and check that address instead.

Additionally, document clearly that allowlisting the router address is equivalent to opening the pool to all users.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   // alice is the only allowed swapper
  allowedSwapper[pool][bob]   = false  // bob is explicitly excluded

Step 1 (admin, reasonable action):
  swapExtension.setAllowedToSwap(pool, address(router), true)
  // admin intends: "let users trade through the router"
  // actual effect: allowedSwapper[pool][router] = true

Step 2 (bob, unprivileged):
  router.exactInputSingle(ExactInputSingleParams{
      pool: pool,
      ...
      extensionData: ""
  })
  // router calls pool.swap(...) with msg.sender = router
  // pool calls _beforeSwap(sender=router, ...)
  // extension checks allowedSwapper[pool][router] → true
  // swap executes; bob receives output tokens

Result:
  bob, who is explicitly excluded from the allowlist, successfully
  swaps against the curated pool. The allowlist invariant is broken.
``` [6](#0-5) [7](#0-6) [4](#0-3)

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
