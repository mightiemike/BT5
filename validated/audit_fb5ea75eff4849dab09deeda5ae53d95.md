### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the end user. The allowlist therefore gates the router address, not the individual swapper. Any pool admin who allowlists the router (required for any allowlisted user to use the router) simultaneously opens the gate to every user on the network.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) calls `pool.swap()`, the pool's `msg.sender` is the router contract: [4](#0-3) 

The router does **not** forward the original caller's address to the pool. The original caller is stored only in transient storage for the payment callback, invisible to the extension: [5](#0-4) 

Therefore, for every router-mediated swap, the allowlist evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][end_user]`.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` intends to restrict swaps to a specific set of addresses (e.g., KYC-verified users). To let those users access the pool through the standard periphery router, the admin must add the router to the allowlist. The moment the router is allowlisted, the check `allowedSwapper[pool][router]` passes for **every** caller of the router, including addresses that were never individually approved. The allowlist is completely neutralized for all router-mediated swaps. Any user can trade on the curated pool by routing through `MetricOmmSimpleRouter`, receiving oracle-priced output that the pool was designed to restrict.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing entry point documented and deployed alongside the protocol. Pool admins who configure a swap allowlist will naturally expect the router to work for their approved users and will allowlist it. The bypass requires no special privileges, no flash loans, and no unusual token behavior — any EOA can call `exactInputSingle` on the router pointing at the curated pool.

---

### Recommendation

Pass the economically relevant actor — the original end user — through to the extension, not the intermediate contract. Two concrete options:

1. **Router-side**: Have the router encode the original `msg.sender` inside `extensionData` and document a convention for allowlist extensions to decode and verify it. This is fragile because it relies on off-chain coordination.

2. **Extension-side (preferred)**: Change `beforeSwap` to check the `recipient` argument (which the router sets to the user-supplied `params.recipient`) or, better, redesign the hook signature so the pool passes both the immediate caller and the originating user. The cleanest fix is for `MetricOmmPool.swap` to accept an explicit `originator` parameter that the router populates with `msg.sender` before calling the pool, and for `_beforeSwap` to forward that value as the identity the allowlist gates.

---

### Proof of Concept

```
Setup:
  1. Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
  2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is KYC'd
  3. Pool admin calls setAllowedToSwap(pool, router, true)  // needed so alice can use the router

Attack:
  4. Bob (not allowlisted) calls:
       router.exactInputSingle({
           pool:          <curated pool>,
           recipient:     bob,
           zeroForOne:    true,
           amountIn:      X,
           ...
       })

  5. Router calls pool.swap(bob, true, X, ...) with msg.sender = router.
  6. Pool calls extension.beforeSwap(router, bob, ...) with msg.sender = pool.
  7. Extension evaluates: allowedSwapper[pool][router] == true  → passes.
  8. Bob receives oracle-priced token1 output from the curated pool.
     The allowlist was never consulted for Bob's address.
``` [6](#0-5) [7](#0-6) [1](#0-0)

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
