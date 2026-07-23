### Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Actual Swapper, Enabling Allowlist Bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user routes through `MetricOmmSimpleRouter`, that `sender` is the **router's address**, not the original user. A pool admin who allowlists the router to enable router-mediated swaps for their curated users inadvertently opens the gate to every user, because the extension cannot distinguish individual users behind the same router address.

---

### Finding Description

**Root cause — wrong actor bound to the allowlist check.**

`MetricOmmPool.swap` calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value as the `sender` parameter of `IMetricOmmExtensions.beforeSwap`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever address called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly: [4](#0-3) 

At that point `msg.sender` inside `pool.swap()` is the **router contract**, so `sender` delivered to the extension is the router address. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Trigger path.**

A pool admin who wants their allowlisted users to be able to use the router must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, the extension passes for every caller of the router — allowlisted or not — because the identity check collapses to a single bit: "is the router allowed?" The extension's per-user mapping becomes unreachable for any router-mediated swap.

The same collapse occurs for every multi-hop path in `exactInput` and `exactOutput`, where intermediate hops are also called by the router: [5](#0-4) 

---

### Impact Explanation

Any non-allowlisted user can trade on a curated pool by routing through `MetricOmmSimpleRouter` whenever the router address is in the pool's allowlist. The pool admin's intent — to restrict trading to a specific set of addresses — is silently defeated. Depending on the pool's purpose (e.g., institutional-only, KYC-gated, or LP-protected), this allows unauthorized price impact, fee extraction, or LP value leakage by actors the admin explicitly excluded.

---

### Likelihood Explanation

The bypass requires the router to be allowlisted. A pool admin who wants their curated users to access the router has no other option: the extension provides no mechanism to allowlist "user X via the router." Allowlisting the router is therefore a natural and expected administrative action, making the precondition reachable in any production deployment that combines `SwapAllowlistExtension` with `MetricOmmSimpleRouter`.

---

### Recommendation

The `sender` argument passed to `beforeSwap` must represent the **economically responsible actor**, not the intermediary contract. Two viable fixes:

1. **Extension-data forwarding**: The router encodes the original `msg.sender` into `extensionData`; the extension decodes and checks that address. The pool admin must trust the router to supply honest data, so this requires the router to be a trusted forwarder.
2. **Separate allowlist for routers**: Distinguish between "this router is a trusted forwarder" and "this user is allowed to swap." The extension checks the decoded user from extension data only when `sender` is a known trusted router.

Either way, the extension must never treat the router address as the identity to gate.

---

### Proof of Concept

```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Pool deployed with ext as beforeSwap hook

// Admin allowlists the router so their users can use it
vm.prank(poolAdmin);
ext.setAllowedToSwap(address(pool), address(router), true);

// Non-allowlisted attacker routes through the router
vm.prank(attacker); // attacker NOT in allowedSwapper[pool]
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    tokenIn: token0,
    recipient: attacker,
    amountIn: 1e18,
    amountOutMinimum: 0,
    zeroForOne: true,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// Swap succeeds: extension checked allowedSwapper[pool][router] == true
// attacker traded on a pool they were never allowlisted for
```

The `beforeSwap` check at line 37 of `SwapAllowlistExtension.sol` evaluates `allowedSwapper[pool][router]` (true) and never inspects the original `attacker` address, completing the bypass. [6](#0-5)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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
