### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Allowing Any User to Bypass Swap Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which is `msg.sender` of `MetricOmmPool.swap`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. To allow allowlisted users to use the router, the pool admin must allowlist the router address itself — which then opens the pool to every user who calls the router, completely defeating the curation policy.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-L240
_beforeSwap(
    msg.sender,   // <-- always the direct caller of pool.swap
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension:

```solidity
// ExtensionCalling.sol L162-L165
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks `sender` against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L37-L39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-L80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

The pool's `msg.sender` is the router contract address. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

The same applies to `exactInput` (all hops call `pool.swap` from the router) and `exactOutput` (recursive callback hops call `pool.swap` from the previous pool in the chain).

The pool admin faces an impossible choice:
- **Do not allowlist the router**: allowlisted users cannot use the router at all.
- **Allowlist the router**: every user who calls `router.exactInputSingle` passes the check, because `sender = router` is allowlisted — the allowlist is nullified for all router-mediated swaps.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict swaps to specific participants (e.g., KYC'd addresses, institutional counterparties) cannot enforce that restriction when the public `MetricOmmSimpleRouter` is available. Any unprivileged user can call `router.exactInputSingle(pool=curatedPool, ...)` and, if the router is allowlisted, execute a swap that the pool admin intended to block. This allows unauthorized users to trade against oracle-anchored prices in a pool not designed for them, extracting value from LPs or disrupting the pool's intended operation.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is a public, permissionless contract. Any user can call it with any pool address. The bypass requires no special privileges, no flash loans, and no complex setup — only knowledge that the router is allowlisted on the target pool. Pool admins who want their allowlisted users to be able to use the router (the standard UX path) will inevitably allowlist the router, triggering the bypass for all users.

---

### Recommendation

The `beforeSwap` hook should receive and check the **economically relevant actor** — the end user — not the intermediate caller. Two approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between router and extension.

2. **Check `sender` in the extension but also accept the router as a transparent forwarder**: The extension reads the router's stored payer from transient storage (analogous to how the router stores payer context in `_setNextCallbackContext`). This requires the extension to be router-aware.

3. **Simplest fix**: Change `SwapAllowlistExtension.beforeSwap` to check the first non-zero argument that represents the true initiator, or require the router to pass the original `msg.sender` as the `sender` argument to `pool.swap` rather than relying on `msg.sender` of the pool call.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Admin calls `setAllowedToSwap(pool, userA, true)` — only `userA` is allowed.
3. Admin also calls `setAllowedToSwap(pool, router, true)` so that `userA` can use the router.
4. Unprivileged `userB` (not allowlisted) calls:
   ```solidity
   router.exactInputSingle(ExactInputSingleParams({
       pool: curatedPool,
       recipient: userB,
       zeroForOne: true,
       amountIn: X,
       ...
   }));
   ```
5. The router calls `curatedPool.swap(...)` with `msg.sender = router`.
6. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[curatedPool][router]` → `true`.
7. The swap executes. `userB` has bypassed the allowlist. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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
