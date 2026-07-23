### Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Any Caller to Bypass the Configured Swap Guard — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the immediate caller of `pool.swap()` (the `sender` argument the pool forwards), not the originating end user. When any user routes through `MetricOmmSimpleRouter`, the router contract becomes `sender`. If the pool admin allowlists the router address to enable router-mediated swaps for authorized users, every unprivileged caller can bypass the swap allowlist by routing through the same public router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as the first argument and checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the pool calls the extension) and `sender` is `msg.sender` of the pool's own `swap()` call — i.e., whoever called the pool directly.

`MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap`: [2](#0-1) 

`ExtensionCalling._beforeSwap` then forwards that value verbatim to the extension: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router is the direct caller of `pool.swap()`:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [4](#0-3) 

So `sender` arriving at the extension is the **router address**, not the end user. The extension evaluates `allowedSwapper[pool][router]`.

This creates an irreconcilable dilemma for the pool admin:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Authorized users cannot use the router at all |
| Allowlist the router | **Every** user — authorized or not — can bypass the allowlist via the router |

There is no configuration that simultaneously allows authorized users to use the router and blocks unauthorized users from doing the same. The `extensionData` field is forwarded from the router to the extension but the `SwapAllowlistExtension` never reads it, so the end user's identity is permanently lost. [1](#0-0) 

---

### Impact Explanation

The swap allowlist is the pool admin's primary mechanism for restricting pool access — e.g., KYC/AML compliance, protocol-only pools, or staged rollouts. Once the router is allowlisted (a necessary step for any authorized user to use the router), the guard is fully neutralized for all router-mediated paths. Any unprivileged caller can execute swaps on a pool that was explicitly configured to block them. This is a direct admin-boundary break: a pool admin-configured guard is bypassed by an unprivileged path through a public periphery contract.

---

### Likelihood Explanation

Medium. The bypass requires the pool admin to have allowlisted the router address. However, this is the expected operational step whenever the pool admin wants authorized users to be able to use the standard router. Any user who knows the router address (it is a public, deployed contract) can then exploit the bypass. No special privileges, flash loans, or oracle manipulation are required.

---

### Recommendation

The `SwapAllowlistExtension` must identify the originating end user, not the immediate pool caller. Two viable approaches:

1. **`extensionData` forwarding**: The router encodes `msg.sender` into `extensionData` before calling the pool. The extension decodes and checks that address. This requires a coordinated change to the router and the extension.

2. **Separate allowlist for routers with per-user sub-checks**: Introduce a two-tier check — if `sender` is an approved router, decode the end user from `extensionData` and check that address against the allowlist.

The current design where `sender` is the immediate pool caller is fundamentally incompatible with a router-mediated access-control model.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `beforeSwap`.
2. Pool admin allowlists user A directly: `setAllowedToSwap(pool, userA, true)`.
3. Pool admin allowlists the router so user A can use it: `setAllowedToSwap(pool, router, true)`.
4. User B (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(...)` — `msg.sender` of `pool.swap()` = router.
6. Pool calls `extension.beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. User B's swap executes on the restricted pool, bypassing the configured guard entirely. [1](#0-0) [5](#0-4)

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
