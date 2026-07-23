### Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Allowing Any User to Bypass Swap Allowlist via Router - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, not the original user. If the pool admin allowlists the router to enable router-mediated swaps, every user — including explicitly disallowed ones — can bypass the individual allowlist by routing through the router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap`, the pool calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim as the `sender` parameter to every configured extension: [2](#0-1) 

**Step 2 — `SwapAllowlistExtension` checks `sender` (the immediate caller of `pool.swap()`).**

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

`msg.sender` here is the pool (correct). `sender` is whoever called `pool.swap()`.

**Step 3 — The router calls `pool.swap()` directly, making itself the `sender`.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` without forwarding the original user's address: [4](#0-3) 

The original user's address is stored only in transient storage for the payment callback (`_setNextCallbackContext`), never passed to the pool or the extension. The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

**Step 4 — The pool admin faces an impossible choice.**

To allow router-mediated swaps on a curated pool, the admin must call:
```solidity
extension.setAllowedToSwap(pool, address(router), true);
``` [5](#0-4) 

Once the router is allowlisted, the extension's check `allowedSwapper[pool][router]` returns `true` for every call that arrives through the router, regardless of who the original user is. The individual per-user allowlist is completely bypassed.

**Contrast with `DepositAllowlistExtension`.**

The deposit extension correctly gates by `owner` (the economic beneficiary of the minted shares), not by `sender` (the payer/caller): [6](#0-5) 

The swap extension has no equivalent design — it gates by the immediate caller, which collapses to the router address for all router-mediated swaps.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is a curated pool: only explicitly approved addresses may trade. The bypass allows any unprivileged user to trade on the curated pool by routing through `MetricOmmSimpleRouter`. Consequences include:

- Unauthorized users drain LP liquidity at oracle prices on pools intended for restricted counterparties.
- The curation invariant ("only allowlisted swappers may trade") is silently violated for every router-mediated swap once the router is allowlisted.
- LP principal is at direct risk if the pool's risk model assumes only vetted counterparties.

**Severity: High** — direct loss of LP-owned assets above Sherlock thresholds; broken core pool functionality (allowlist guard fails open on the standard public swap path).

---

### Likelihood Explanation

**High.** The router is the primary user-facing swap interface. Any pool admin who deploys a curated pool and also wants users to access it through the standard router must allowlist the router. This is the expected operational pattern. The bypass is therefore reachable on every production curated pool that supports router-mediated swaps.

The attacker requires no special privileges: they only need to call `router.exactInputSingle()` with a valid swap path.

---

### Recommendation

The extension must gate the **original user**, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool. The `SwapAllowlistExtension` decodes and checks that address instead of `sender`. This requires a coordinated change in the router and the extension.

2. **Check `sender` only when `sender` is not a known router**: The extension maintains a registry of trusted routers. When `sender` is a trusted router, the extension reads the original user from `extensionData`. When `sender` is not a router, it checks `sender` directly.

The deposit extension's pattern — checking the economically relevant actor (`owner`) rather than the immediate caller — is the correct model to follow.

---

### Proof of Concept

```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Pool deployed with ext as beforeSwap extension

// Admin allowlists the router so legitimate users can swap via router
vm.prank(admin);
ext.setAllowedToSwap(address(pool), address(router), true);

// Admin does NOT allowlist attacker
// ext.allowedSwapper[pool][attacker] == false

// Attacker bypasses allowlist via router
vm.prank(attacker);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: token0,
        recipient: attacker,
        deadline: block.timestamp,
        amountIn: 1000e18,
        amountOutMinimum: 0,
        zeroForOne: true,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// ✓ Swap succeeds — extension checked allowedSwapper[pool][router] == true
// ✓ Attacker traded on a curated pool without being individually allowlisted
```

The pool's `swap` receives `msg.sender = router`; the extension checks `allowedSwapper[pool][router]` → `true`; the attacker's swap executes at oracle price against LP funds.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
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
  }
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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
