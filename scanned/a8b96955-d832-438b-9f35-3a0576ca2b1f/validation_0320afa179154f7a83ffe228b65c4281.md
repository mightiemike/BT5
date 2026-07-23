Now I have all the information needed. Let me trace the exact call path for the swap allowlist bypass.

### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Allowing Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the end user. The extension therefore checks whether the **router** is allowlisted, not the actual trader. A pool admin who allowlists the router to enable router-mediated swaps inadvertently opens the gate to every user on-chain, defeating the entire purpose of the allowlist.

---

### Finding Description

**Call path:**

```
user → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ..., extensionData)   // msg.sender = router
              → _beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  ← wrong actor checked
```

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` the pool sees: [4](#0-3) 

The actual end-user address (`msg.sender` of the router call) is stored only in transient storage for the payment callback and is **never forwarded to the pool or the extension**. [5](#0-4) 

---

### Impact Explanation

Two mutually exclusive failure modes exist, both fund-impacting:

**Mode A — Allowlist bypass (High):** The pool admin allowlists the router address so that allowlisted users can trade through the standard periphery. Because the extension only sees `sender = router`, every user on-chain can now call `router.exactInputSingle()` and pass the check. The curated pool's access control is completely nullified; any unauthorized address can execute swaps and drain LP value at oracle prices.

**Mode B — Broken core functionality (Medium):** The pool admin allowlists individual user addresses but does not allowlist the router. Allowlisted users who call the router receive `NotAllowedToSwap` because the extension sees `sender = router` (not allowlisted). They are forced to call `pool.swap()` directly, which requires implementing the `IMetricOmmSwapCallback` interface themselves. The router — the protocol's primary user-facing swap path — is unusable for any allowlisted pool.

In Mode A, the loss is direct: unauthorized traders execute swaps against LP capital at oracle-derived prices, extracting value the pool admin intended to restrict.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the documented, primary swap entrypoint for end users. Any pool that deploys `SwapAllowlistExtension` and also wants users to use the router will encounter Mode A or Mode B immediately upon first use. No special preconditions, flash loans, or multi-block timing are required. A single `exactInputSingle` call from any address is sufficient to trigger the bypass in Mode A.

---

### Recommendation

Pass the **original end-user address** through the swap path so the extension can gate the correct actor. Two approaches:

1. **Preferred — add a `payer` field to the swap interface:** Have the router pass `msg.sender` (the actual user) as an explicit `payer` argument to `pool.swap()`, and forward it to extensions alongside `sender`. The extension then checks `allowedSwapper[pool][payer]`.

2. **Simpler — check `recipient` instead of `sender`:** For single-hop swaps the user typically sets `recipient` to their own address. This is not robust for multi-hop flows where intermediate recipients are the router itself.

3. **Extension-side workaround:** The extension could accept a user address encoded in `extensionData` and verify it against a signature or trusted forwarder, but this shifts the burden to every caller and is error-prone.

The cleanest fix is option 1: extend `IMetricOmmPoolActions.swap` and `IMetricOmmExtensions.beforeSwap` with a `payer` parameter populated by the router from its own `msg.sender`.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `allowedUser` is allowlisted.
swapExtension.setAllowedToSwap(address(pool), allowedUser, true);
// Router is NOT explicitly allowlisted.

// Step 1: allowedUser tries router — REVERTS (router not in allowlist).
vm.prank(allowedUser);
router.exactInputSingle(...);  // NotAllowedToSwap

// Step 2: admin adds router to allowlist so allowedUser can use the router.
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Step 3: allowedUser can now use the router — passes.
vm.prank(allowedUser);
router.exactInputSingle(...);  // succeeds

// Step 4: BYPASS — any random address also passes because extension sees sender=router.
vm.prank(bannedUser);          // bannedUser never allowlisted
router.exactInputSingle(...);  // also succeeds — allowlist bypassed
```

The root cause is that `SwapAllowlistExtension.beforeSwap` receives `sender = address(router)` in both Step 3 and Step 4, making them indistinguishable. [6](#0-5) [7](#0-6) [8](#0-7)

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
