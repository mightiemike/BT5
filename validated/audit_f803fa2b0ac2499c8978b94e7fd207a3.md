### Title
SwapAllowlistExtension Gates the Router Address Instead of the Originating User, Allowing Any Caller to Bypass the Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool binds to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the originating user. The allowlist therefore gates the router address rather than the actual trader. If the pool admin allowlists the router so that allowlisted users can trade through it, every non-allowlisted user gains unrestricted swap access by routing through the same public contract.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that value verbatim to every extension in `BEFORE_SWAP_ORDER`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates the allowlist against that `sender`: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) calls `pool.swap`, the pool's `msg.sender` is the router contract: [4](#0-3) 

The originating user's address is never forwarded to the pool or to the extension. The extension therefore evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`.

This creates an inescapable dilemma for the pool admin:

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | Allowlisted users cannot trade through the router at all; they must call the pool directly |
| Router **allowlisted** | Every non-allowlisted user bypasses the restriction by routing through the public router |

The second branch is the exploitable path. A pool admin who wants allowlisted users to access the router must allowlist the router address, which silently opens the gate to all users.

### Impact Explanation

The `SwapAllowlistExtension` is the protocol's primary mechanism for restricting swap access to a curated set of addresses (e.g., KYC'd participants, whitelisted market makers, or private pools). Bypassing it allows any unprivileged user to execute swaps in a pool that was explicitly configured to deny them. Depending on pool composition and oracle pricing, this can result in:

- Unauthorized extraction of LP assets at oracle-quoted prices from a pool intended to be private.
- Disruption of pools whose liquidity was sized for a known, bounded set of traders.

The loss is direct and repeatable: every swap executed by a non-allowlisted user through the router drains LP value that the allowlist was meant to protect.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is a public, permissionless contract. Any user can call it. The only precondition for the bypass is that the pool admin has allowlisted the router address — a step that is operationally necessary the moment any allowlisted user wants to trade through the router rather than calling the pool directly. No privileged access, no special tokens, and no malicious setup are required.

### Recommendation

The `sender` value passed to extension hooks must represent the originating user, not the intermediate router. Two approaches:

1. **Router-side**: Have the router encode the originating `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and check that value instead of the raw `sender` argument.
2. **Extension-side**: Document that `sender` is the immediate pool caller and change `SwapAllowlistExtension` to check `recipient` when `sender` is a known router, or require pools using this extension to be called directly (no router).

The cleanest fix is approach 1: the router appends `abi.encode(msg.sender)` to `extensionData` before forwarding to the pool, and the extension decodes the true originator from that payload.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   (alice is the only allowlisted user)
  allowAllSwappers[pool]      = false

Step 1 — pool admin allowlists the router so alice can use it:
  allowedSwapper[pool][router] = true

Step 2 — bob (not allowlisted) calls:
  router.exactInputSingle({
    pool:      pool,
    recipient: bob,
    zeroForOne: true,
    amountIn:  X,
    ...
  })

Step 3 — router calls pool.swap(bob, true, X, ...)
  pool.msg.sender = router
  _beforeSwap(sender=router, ...)

Step 4 — SwapAllowlistExtension.beforeSwap:
  allowedSwapper[pool][router] == true  →  check passes

Step 5 — swap executes; bob receives output tokens.
  The allowlist was never consulted for bob's address.
``` [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-41)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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
