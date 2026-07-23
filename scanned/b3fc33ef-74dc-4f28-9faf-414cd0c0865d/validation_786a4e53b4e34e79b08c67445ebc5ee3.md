### Title
`SwapAllowlistExtension` checks router address as swapper identity, allowing any user to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument it receives from the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so `sender` is the router address — not the actual end user. If the pool admin allowlists the router (which is required for any router-mediated swap to succeed), every user on the network can bypass the per-user allowlist and trade on a pool that is supposed to be restricted.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever was passed in: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`) calls `pool.swap(...)`, the pool's `msg.sender` is the router contract: [4](#0-3) 

Therefore the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The pool admin faces an impossible choice:

| Router allowlisted? | Effect |
|---|---|
| Yes | Every user on the network can swap through the router — per-user allowlist is dead |
| No | Even allowlisted users cannot use the router — core periphery is unusable for this pool |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

---

### Impact Explanation

A pool deployer who configures `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., a private institutional market, a KYC-gated pool, or a pool with subsidised pricing for specific LPs) cannot enforce that restriction for router-mediated swaps. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` and trade on the restricted pool, receiving the same execution as an allowlisted party. If the pool offers favourable pricing or is meant to be a closed market, the attacker captures value that was reserved for authorised participants — a direct loss of owed LP assets or protocol-fee revenue.

---

### Likelihood Explanation

The bypass requires no special privilege: any user with knowledge of the pool address and the router address can execute it. The router is a public, deployed periphery contract. The bypass is unconditional whenever the pool admin has allowlisted the router (which they must do for any router-mediated swap to work). Likelihood is **High**.

---

### Recommendation

The `SwapAllowlistExtension` must gate the real end-user, not the intermediary. Two complementary fixes:

1. **Router-level**: `MetricOmmSimpleRouter` should store the original `msg.sender` in transient storage and expose it via a callback or pass it as part of `extensionData`, so the extension can read the true initiator.

2. **Extension-level**: `SwapAllowlistExtension.beforeSwap` should read the actual user from `extensionData` (signed or forwarded by the router) rather than trusting the `sender` argument, which is always the immediate caller of `pool.swap`.

Until this is resolved, pools that require per-user swap gating must instruct users to call `pool.swap` directly and must **not** allowlist the router.

---

### Proof of Concept

```
Setup
─────
1. Pool admin deploys pool with SwapAllowlistExtension configured.
2. Admin calls setAllowedToSwap(pool, ALICE, true)   // ALICE is the only allowed swapper
3. Admin calls setAllowedToSwap(pool, router, true)  // required so ALICE can use the router

Attack
──────
4. BOB (not allowlisted) calls:
       router.exactInputSingle({
           pool:      <restricted pool>,
           tokenIn:   token0,
           recipient: BOB,
           amountIn:  X,
           ...
       })

5. Router calls pool.swap(recipient=BOB, ...)
   → pool's msg.sender = router

6. Pool calls _beforeSwap(sender=router, ...)
   → ExtensionCalling encodes (sender=router, ...) and calls SwapAllowlistExtension

7. SwapAllowlistExtension checks:
       allowedSwapper[pool][router]  →  true  (admin allowlisted the router in step 3)
   → hook passes, swap executes

Result: BOB trades on a pool restricted to ALICE, receiving the same execution terms.
        The per-user allowlist is completely bypassed.
``` [3](#0-2) [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L228-241)
```text
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
