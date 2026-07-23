### Title
SwapAllowlistExtension Gates Router Address Instead of End User, Allowing Any User to Bypass Curated Pool Swap Restrictions - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap()` checks the `sender` argument against the per-pool allowlist. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` — and therefore the `sender` forwarded to the extension — is the **router contract address**, not the end user. A pool admin who allowlists the router to enable router-mediated swaps for legitimate users simultaneously opens the allowlist to every user on the network, because any caller of the router will pass the router's address as `sender` to the extension check.

### Finding Description

`MetricOmmPool.swap()` captures `msg.sender` and forwards it as the `sender` argument through `ExtensionCalling._beforeSwap()` to every configured extension. [1](#0-0) 

`ExtensionCalling._beforeSwap()` encodes that value verbatim as the first argument of the extension call. [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then uses `msg.sender` (the calling pool) as the pool key and the received `sender` argument as the identity to gate:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle()` (or any `exact*` variant) calls `pool.swap()`, the pool's `msg.sender` is the **router**, not the end user: [4](#0-3) 

The router does not forward the original caller's address as `sender`; it simply calls `pool.swap()` directly. Consequently, the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][end_user]`.

This creates an irresolvable dilemma for the pool admin:

- **Option A — Do not allowlist the router:** Individually allowlisted users cannot use the router at all, because the router address is not in the allowlist. Legitimate users are locked out of the supported periphery path.
- **Option B — Allowlist the router:** The check becomes `allowedSwapper[pool][router] == true`, which passes for every caller of the router regardless of their identity. The allowlist is completely bypassed for all router-mediated swaps.

The protocol's own audit-target documentation confirms the intended invariant: *"the hook must gate the same actor the pool designers thought they were allowlisting"* and *"assert the hook cannot be bypassed by routing through an intermediate public contract."* [5](#0-4) 

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` to restrict trading to specific counterparties loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Unauthorized users can execute swaps against the pool's liquidity, draining LP value in ways the pool admin explicitly intended to prevent. The allowlist guard — the sole mechanism protecting the curated pool — fails open on the primary supported periphery path.

### Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical user-facing swap interface. Any pool admin who wants allowlisted users to be able to use the router must allowlist the router address, which is the exact action that opens the bypass. The trigger requires no special privileges: any unprivileged user calls `router.exactInputSingle()` targeting the curated pool.

### Recommendation

The extension must gate the economically relevant actor, not the direct caller of `pool.swap()`. Two viable approaches:

1. **Pass original caller in extensionData:** The router encodes `msg.sender` into `extensionData` before calling the pool. `SwapAllowlistExtension.beforeSwap()` decodes and verifies this value. This requires a trusted encoding convention between router and extension.

2. **Check `recipient` as a proxy for the user:** For single-hop swaps the recipient is often the end user, but this is not reliable for multi-hop paths where intermediate recipients are the router itself.

The cleanest fix is approach 1, combined with a check that if `extensionData` is absent (direct pool call), `sender` is used as the gated identity.

### Proof of Concept

```solidity
// Pool admin sets up a curated pool: only `alice` is allowed to swap.
swapExtension.setAllowedToSwap(address(pool), alice, true);

// Pool admin also allowlists the router so alice can use it.
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Now bob (not allowlisted) calls the router.
// pool.swap() sees msg.sender = router → sender = router → allowedSwapper[pool][router] = true → PASSES.
vm.prank(bob);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    tokenIn: address(token0),
    recipient: bob,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// Bob's swap succeeds despite not being on the allowlist.
```

The `SwapAllowlistExtension` check at line 37 evaluates `allowedSwapper[pool][router]` (true) and does not revert, allowing the unauthorized swap to execute against the curated pool's liquidity. [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
  }
```
