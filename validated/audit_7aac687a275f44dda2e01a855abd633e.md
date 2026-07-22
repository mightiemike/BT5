### Title
`SwapAllowlistExtension` checks router address as swapper identity, allowing full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument it receives from the pool. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so `sender` delivered to the extension is the router address — not the actual user. A pool admin who allowlists the router (required for any router-mediated swap to succeed) inadvertently opens the allowlist to every user who routes through the public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly, making the pool's `msg.sender` the router: [4](#0-3) 

The same pattern holds for `exactInput` (intermediate hops use `address(this)` as payer) and `exactOutput`: [5](#0-4) 

The extension therefore sees `sender = router`, never the actual end-user. The pool admin faces an impossible choice:

- **Do not allowlist the router** → every allowlisted user is blocked from using the standard router UX.
- **Allowlist the router** → every non-allowlisted user can bypass the guard by routing through the public router.

There is no configuration that simultaneously enforces per-user allowlisting and permits router-mediated swaps.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, institutional LPs, or protocol-controlled bots) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The bypass is unconditional once the router is allowlisted, requires no special privilege, and is reachable on every `exactInputSingle`, `exactInput`, and `exactOutput` call. This is a direct policy bypass on curated pools — the core invariant that "only allowlisted addresses may swap" is broken.

---

### Likelihood Explanation

Any pool that (a) deploys with `SwapAllowlistExtension` and (b) expects users to interact via the standard periphery router is affected. The router is a first-party, publicly deployed contract; allowlisting it is the natural step a pool admin takes to enable normal UX. The bypass requires no special timing, no flash loan, and no privileged role — any EOA can trigger it by calling the public router.

---

### Recommendation

The extension must gate on the **economic actor**, not the immediate pool caller. Two complementary fixes:

1. **Pass real user identity through the router.** The router already stores the real payer in transient storage (`_getPayer()`). Encode the real user address into `extensionData` before calling the pool, and have `SwapAllowlistExtension` decode and check that value instead of `sender`.

2. **Check `recipient` as a fallback.** For single-hop exact-input swaps the recipient is often the real user; however, this breaks for multi-hop paths where intermediate recipients are the router itself.

The cleanest fix is option 1: the router encodes `msg.sender` into `extensionData`, and the extension decodes and verifies it when `sender` is a known router address.

---

### Proof of Concept

```solidity
// Pool admin sets up a curated pool
pool = factory.deployPool(..., extensionWithSwapAllowlist, ...);

// Admin allowlists Alice but NOT Bob
swapAllowlist.setAllowedToSwap(address(pool), alice, true);

// Admin must also allowlist the router for Alice to use it
swapAllowlist.setAllowedToSwap(address(pool), address(router), true);

// Bob (not allowlisted) calls the router — extension sees sender=router, which IS allowlisted
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: bob,
    zeroForOne: true,
    amountIn: 1e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    tokenIn: token0,
    extensionData: ""
}));
// ✓ swap succeeds — Bob bypassed the allowlist
```

The root cause is that `SwapAllowlistExtension.beforeSwap` receives `sender = address(router)` and checks `allowedSwapper[pool][router]`, which is `true`, so the guard passes for Bob. [6](#0-5)

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
