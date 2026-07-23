### Title
SwapAllowlistExtension Checks Router Address Instead of Original EOA, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument it receives, which is the pool's `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the original EOA. If the pool admin allowlists the router (the natural step to let their curated users trade via the router), every unprivileged user can bypass the allowlist by routing through the same public contract.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value as the first argument of the `beforeSwap` call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is in the per-pool allowlist: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point the pool's `msg.sender` is the **router address**, so the extension receives `sender = router_address` and evaluates `allowedSwapper[pool][router_address]` — not `allowedSwapper[pool][original_EOA]`.

The same mismatch applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

---

### Impact Explanation

A pool admin who deploys a curated pool (e.g., KYC-only trading) with `SwapAllowlistExtension` faces an inescapable dilemma:

- **Do not allowlist the router** → allowlisted EOAs cannot use `MetricOmmSimpleRouter` at all; the standard periphery path is broken for legitimate users.
- **Allowlist the router** → `allowedSwapper[pool][router] = true`, so the extension passes for every call that arrives through the router, regardless of who the original EOA is. Any unprivileged user calls `MetricOmmSimpleRouter.exactInputSingle` targeting the curated pool and the guard silently passes.

The second path is the natural operational choice (admins want their users to use the router), and it results in a **complete bypass** of the swap allowlist. The pool's curation invariant — only approved addresses may trade — is permanently broken for all router-mediated swaps.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the standard, documented periphery entry point for swaps.
- Pool admins who configure a `SwapAllowlistExtension` will routinely allowlist the router so their approved users can trade normally.
- Once the router is allowlisted, the bypass requires no special setup: any EOA calls a public router function.
- The mismatch is invisible in unit tests that call the extension directly (with `vm.prank(address(pool))`), because those tests supply the intended EOA as `sender` rather than the router address. [6](#0-5) 

---

### Recommendation

The extension must recover the original EOA rather than accepting the intermediary address. Two sound approaches:

1. **Pass the original initiator through the router.** The router already stores the original `msg.sender` in transient storage as the payer. Thread it through as an additional field in `extensionData` or as a dedicated parameter, and have the extension read it from there.

2. **Gate on `msg.sender` at the router level.** Add an allowlist check inside `MetricOmmSimpleRouter` before forwarding to the pool, so the router itself enforces who may initiate swaps on curated pools.

Either way, the identity checked by `SwapAllowlistExtension` must be the address that economically initiates the swap, not the intermediate contract that relays it.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, router, true)   // to let approved users use the router
  admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  attacker (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({
        pool:      curated_pool,
        recipient: attacker,
        zeroForOne: true,
        amountIn:  X,
        ...
    })

  Flow:
    router → pool.swap(recipient, ...)
      pool: msg.sender = router_address
      pool: _beforeSwap(sender=router_address, ...)
        SwapAllowlistExtension.beforeSwap(sender=router_address, ...)
          allowedSwapper[pool][router_address] == true  ✓  (admin set this)
          → does NOT revert
      swap executes fully
      attacker receives output tokens

Result:
  attacker trades on a pool that was supposed to be restricted to approved addresses only.
  The allowlist invariant is broken for all router-mediated swaps.
``` [7](#0-6) [8](#0-7)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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

**File:** metric-periphery/test/extensions/SwapAllowlistSubExtension.t.sol (L32-38)
```text
  function test_passesWhenSwapperAllowed() public {
    vm.prank(admin);
    extension.setAllowedToSwap(address(pool), swapper, true);

    vm.prank(address(pool));
    extension.beforeSwap(swapper, address(0), false, 0, 0, 0, 0, 0, "");
  }
```
