### Title
`SwapAllowlistExtension` checks the router's address instead of the actual user, making the allowlist bypassable for all router-mediated swaps — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the pool admin allowlists the router (the only way to enable router-mediated swaps for allowlisted users), every address in the system can bypass the allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist: [1](#0-0) 

`sender` is populated by `ExtensionCalling._beforeSwap`, which encodes `msg.sender` of the pool's `swap` call as the first argument: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly: [3](#0-2) 

At that point the pool's `msg.sender` is the router contract. The pool therefore passes `sender = router_address` to the extension. The extension checks `allowedSwapper[pool][router_address]`, not the identity of the human caller.

This creates an irresolvable configuration trap for any pool admin who deploys a pool with `SwapAllowlistExtension`:

| Admin intent | Required config | Actual outcome |
|---|---|---|
| Allow Alice to swap directly | `allowedSwapper[pool][alice] = true` | Alice can swap directly; Alice **cannot** use the router |
| Allow Alice to swap via router | `allowedSwapper[pool][router] = true` | **Every address** can swap via the router — allowlist is fully bypassed |
| Allow Alice both ways | Both entries set | Alice can swap both ways; **every address** can also use the router |

There is no configuration that simultaneously permits allowlisted users to use the router and blocks non-allowlisted users from doing the same.

The `extensionData` field forwarded by the router is user-controlled and cannot be trusted for identity verification without an additional signature scheme that does not exist in the current implementation: [4](#0-3) 

---

### Impact Explanation

Any pool that uses `SwapAllowlistExtension` and allowlists the router (a necessary step to enable router-mediated swaps for legitimate users) becomes fully open to all swappers. The allowlist — the pool's primary access-control mechanism — is rendered inoperative. Non-allowlisted addresses can execute swaps, receive output tokens, and drain liquidity from a pool that was explicitly configured to restrict access. This is a direct loss of the access-control invariant with fund-impacting consequences for LPs who deposited under the assumption that only vetted counterparties could trade against their liquidity.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing entry point documented and shipped with the protocol. Any pool admin who deploys a restricted pool and then tries to make it usable through the router will naturally allowlist the router address, triggering the bypass. The misconfiguration is not obvious: the admin believes they are enabling router access for allowlisted users, not opening the pool to everyone. The bypass requires no special privileges, no flash loans, and no unusual token behavior — any address can call the router.

---

### Recommendation

The extension must verify the identity of the **human caller**, not the intermediary contract. Two viable approaches:

1. **Require the actual user address in `extensionData` with an off-chain signature** — the extension verifies the signature and checks the recovered address against the allowlist. The router must forward the signed payload.
2. **Check `sender` only when it is not a known periphery contract; otherwise read the user address from a trusted field in `extensionData`** — requires the router to embed `msg.sender` in a tamper-evident way (e.g., signed or verified against a factory registry).

The simplest safe default is to document that `SwapAllowlistExtension` is incompatible with any shared router and must only be used with direct pool calls or a purpose-built router that embeds and authenticates the originating user address.

---

### Proof of Concept

```
Setup:
  pool deployed with SwapAllowlistExtension
  pool admin calls: extension.setAllowedToSwap(pool, alice, true)
  pool admin calls: extension.setAllowedToSwap(pool, router, true)
    (required so alice can use the router)

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  router calls:
    pool.swap(bob, zeroForOne, amount, priceLimit, "", extensionData)
    // pool.msg.sender == router

  pool calls:
    extension.beforeSwap(sender=router, ...)
    // checks allowedSwapper[pool][router] == true  ✓ passes

  bob receives output tokens despite never being allowlisted.
``` [1](#0-0) [2](#0-1) [4](#0-3)

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
