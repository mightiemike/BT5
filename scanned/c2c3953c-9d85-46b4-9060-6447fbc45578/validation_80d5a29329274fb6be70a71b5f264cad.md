### Title
SwapAllowlistExtension Allowlist Bypassed via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks the router's address rather than the original user's address. A pool admin cannot simultaneously allow allowlisted users to trade through the router and block non-allowlisted users: either the router is allowlisted (opening the gate to everyone) or it is not (blocking all router-mediated swaps, even for allowlisted users).

### Finding Description
`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool calling the extension. [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards whatever `sender` the pool supplies: [2](#0-1) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant) calls `pool.swap(...)`, the pool's `msg.sender` is the router contract. The pool therefore passes `sender = router` into `_beforeSwap`, and the extension evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][originalUser]`. [3](#0-2) 

The router stores the original user only in its own transient payment context (for the callback), never forwarding it to the pool or the extension: [4](#0-3) 

### Impact Explanation
A pool admin who deploys a curated pool with `SwapAllowlistExtension` intends to restrict trading to a specific set of addresses. Two outcomes are possible, both harmful:

1. **Router not allowlisted**: every allowlisted user who routes through `MetricOmmSimpleRouter` is rejected (`NotAllowedToSwap`), making the standard periphery path unusable for legitimate participants.
2. **Router allowlisted** (to fix case 1): `allowedSwapper[pool][router] = true` opens the gate to every address on the network, completely defeating the allowlist. Any non-allowlisted user calls `router.exactInputSingle(...)` and the extension passes.

In case 2, non-allowlisted users can execute swaps on a pool designed to be curated — draining LP assets at prices the pool admin did not intend to expose to the general public, or front-running allowlisted market makers on a pool that was supposed to be private.

### Likelihood Explanation
- `MetricOmmSimpleRouter` is the primary public swap entrypoint in `metric-periphery`.
- Any pool that uses `SwapAllowlistExtension` and expects users to interact through the router is immediately affected.
- No special privileges or unusual conditions are required; a standard `exactInputSingle` call suffices.
- The `DepositAllowlistExtension` correctly gates on `owner` (the economically relevant actor for deposits), making it natural for a pool admin to assume the swap extension similarly gates on the original swapper — but it does not. [5](#0-4) 

### Recommendation
Pass the original swapper identity through the pool's swap call so the extension can check it. Two approaches:

1. **Preferred — thread the original sender**: have the pool expose the original `msg.sender` to extensions as a separate `originator` field distinct from the immediate caller, or have the router pass the user address in `extensionData` and have the extension decode it (with appropriate trust assumptions).
2. **Simpler — check `recipient` or use a dedicated field**: redesign `SwapAllowlistExtension` to gate on the `recipient` (the address that receives output tokens) rather than `sender`, since `recipient` is set by the original user and is not replaced by the router.

Additionally, document clearly that `sender` in `beforeSwap` is the immediate pool caller, not the end-user, so pool admins understand what they are allowlisting.

### Proof of Concept
```
1. Deploy a pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, alice, true)  — only alice should swap.
3. Pool admin calls setAllowedToSwap(pool, router, true) — required so alice can use the router.
4. Bob (not allowlisted) calls router.exactInputSingle({pool, tokenIn, tokenOut, ...}).
5. Pool calls _beforeSwap(sender=router, ...).
6. Extension checks allowedSwapper[pool][router] == true → passes.
7. Bob's swap executes on the curated pool despite not being allowlisted.
```

Alternatively, if the pool admin does NOT allowlist the router:
```
4. Alice (allowlisted) calls router.exactInputSingle({pool, ...}).
5. Extension checks allowedSwapper[pool][router] == false → reverts NotAllowedToSwap.
6. Alice cannot use the standard periphery path at all.
```

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
