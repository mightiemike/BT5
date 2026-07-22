### Title
`SwapAllowlistExtension` checks the router's address instead of the end-user's address, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router's address**, not the end-user's address. A pool admin who allowlists the router to enable router-mediated swaps inadvertently grants every user — including non-allowlisted ones — the ability to bypass the per-user restriction.

---

### Finding Description

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap(); the router when routed
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

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol lines 149-177
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, amountSpecified, priceLimitX64,
     packedSlot0Initial, bidPriceX64, askPriceX64, extensionData)
)
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol lines 31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()`, `sender` = **router address**. The allowlist lookup becomes `allowedSwapper[pool][router]`.

The pool admin faces an impossible choice:
- **Allowlist the router** → every user, including non-allowlisted ones, can swap through the router. The per-user allowlist is completely bypassed.
- **Do not allowlist the router** → no user can use the router, even those individually allowlisted.

There is no path that simultaneously enables router-mediated swaps and enforces per-user restrictions, because the router's address is what the guard sees.

---

### Impact Explanation

Any user can call `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) targeting a pool protected by `SwapAllowlistExtension`. If the pool admin has allowlisted the router (the natural action to enable router usage), the guard passes unconditionally for all users. Non-allowlisted users gain full swap access to a pool intended to be restricted, can extract value at the pool's oracle-anchored prices, and can manipulate the pool's bin state in ways the admin did not intend. This is a direct loss of the access-control invariant with fund-impacting consequences: unauthorized traders can drain liquidity at favorable oracle prices from a pool designed to serve only specific counterparties.

---

### Likelihood Explanation

Likelihood is high. The `MetricOmmSimpleRouter` is the standard user-facing entry point for the protocol. Any pool admin who deploys a `SwapAllowlistExtension`-protected pool and wants their allowlisted users to have a normal UX will allowlist the router. The bypass requires no special privileges, no flash loans, and no unusual token behavior — any EOA can call the router.

---

### Recommendation

The extension must recover the **end-user's identity**, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass the user address in `extensionData`**: The router encodes `msg.sender` (the end-user) into `extensionData` before forwarding to the pool. `SwapAllowlistExtension.beforeSwap` decodes and checks that address. The extension must also verify that `msg.sender` (the pool) is a known pool so the data cannot be spoofed by a direct caller who crafts `extensionData` themselves.

2. **Check `tx.origin` as a fallback**: When `sender` is a known router, fall back to `tx.origin`. This is fragile and generally discouraged but is a minimal patch.

The cleanest fix is option 1: the router explicitly encodes the initiating user in `extensionData`, and the extension verifies both the pool identity and the decoded user address.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use it.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)` with `msg.sender = router`.
6. Pool calls `_beforeSwap(router, ...)` → extension checks `allowedSwapper[pool][router]` → **true** → swap proceeds.
7. Bob successfully swaps in a pool he is not allowlisted for, receiving output tokens he should not have access to. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
