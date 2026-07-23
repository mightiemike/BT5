### Title
`SwapAllowlistExtension` gates the router address instead of the actual end-user swapper, allowing any unprivileged user to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the end user. The extension therefore checks whether the router is allowlisted, not whether the individual user is allowlisted. Any user can bypass a curated pool's swap allowlist by routing through the public router.

---

### Finding Description

**Call chain:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle(params)`.
2. Router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` — here `msg.sender` to the pool is the **router address**.
3. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`, forwarding `sender = router`.
4. `ExtensionCalling._beforeSwap` encodes `sender = router` and dispatches to `SwapAllowlistExtension.beforeSwap`.
5. The extension evaluates `allowedSwapper[pool][router]`, **not** `allowedSwapper[pool][actualUser]`.

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`sender` here is the router, not the end user. The pool admin who intends to allowlist individual traders (e.g., KYC-verified addresses) must also allowlist the router for router-based swaps to work at all. Once the router is allowlisted, **every** user — including those explicitly not in the allowlist — can swap by going through the router.

The `FullMetricExtensionTest` confirms the intended design: it allowlists `address(callers[0])` (the `TestCaller` contract that directly calls the pool), not the end user. This works only because the test bypasses the router. No test exercises the router-mediated path against a pool with `SwapAllowlistExtension` active.

---

### Impact Explanation

A pool admin deploys a curated pool (e.g., KYC-gated, institutional-only) with `SwapAllowlistExtension` and allowlists the router so that approved users can trade via the standard periphery. Any non-allowlisted address can then call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps on the restricted pool. The curation policy is completely ineffective: the pool receives trades from actors it was explicitly configured to exclude, and those actors receive swap output tokens they should not have been able to obtain.

---

### Likelihood Explanation

High. The `MetricOmmSimpleRouter` is the standard, documented swap entry point. Any user who reads the contract or the docs can discover that routing through it changes the `sender` seen by the extension. The pool admin must allowlist the router for the pool to be usable via the periphery at all, so the precondition (router allowlisted) is the normal production configuration, not an edge case.

---

### Recommendation

The extension must identify the **economic actor** (the end user), not the intermediary. Two viable approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted router or a signed payload.
2. **Check `recipient` instead of `sender`**: For swap allowlists the recipient is often the economically relevant party; however, this also has edge cases when `recipient != actual user`.
3. **Dedicated router-aware allowlist**: The extension reads the payer from transient storage set by the router (analogous to how `MetricOmmPoolLiquidityAdder` stores the payer in transient slots), so the real initiator is always available.

---

### Proof of Concept

```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Pool admin allowlists the router so router-based swaps work
ext.setAllowedToSwap(pool, address(router), true);
// Alice is NOT individually allowlisted
// ext.setAllowedToSwap(pool, alice, true);  ← intentionally omitted

// Alice bypasses the allowlist via the router
vm.prank(alice);
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    zeroForOne: false,
    amountIn: 1000,
    amountOutMinimum: 0,
    recipient: alice,
    priceLimitX64: type(uint128).max,
    tokenIn: token1,
    deadline: block.timestamp,
    extensionData: ""
}));
// ✓ swap succeeds — alice bypassed the allowlist
```

**Root cause trace:**

- `MetricOmmPool.swap` passes `msg.sender` (= router) as `sender` to `_beforeSwap`. [1](#0-0) 

- `ExtensionCalling._beforeSwap` forwards that `sender` verbatim to the extension. [2](#0-1) 

- `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` — `msg.sender` is the pool, `sender` is the router. [3](#0-2) 

- The router calls `pool.swap` directly, making itself `msg.sender` to the pool. [4](#0-3)

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
