### Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Enabling Complete Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to its own `msg.sender`. When `MetricOmmSimpleRouter` intermediates a swap, `sender` is the router's address, not the originating user. A pool admin who allowlists the router to enable router-mediated swaps inadvertently opens the gate to every user, completely defeating the allowlist.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes and forwards the `sender` value that the pool received as its own `msg.sender`:

```solidity
// metric-core/contracts/ExtensionCalling.sol
function _beforeSwap(address sender, ...) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
    );
}
``` [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the pool's `msg.sender`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [3](#0-2) 

The same pattern applies to `exactInput` (all hops), `exactOutputSingle`, and `exactOutput`: [4](#0-3) 

The result is that `sender` delivered to the extension is always the router's address, never the originating EOA. The extension has no way to distinguish which user initiated the swap.

**Two broken invariants arise simultaneously:**

1. **Allowlist bypass**: A pool admin who allowlists the router address (a natural step to enable router-mediated swaps) grants every user — including explicitly blocked ones — the ability to swap, because the extension sees only the router and the router is allowlisted.

2. **Allowlisted users locked out of the router**: If the pool admin allowlists individual EOAs but not the router, those EOAs cannot swap through the router even though they are permitted, because the extension sees the router and the router is not allowlisted.

Both outcomes break the core invariant that the allowlist gates the economically relevant actor.

---

### Impact Explanation

**High.** A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Non-allowlisted users can execute swaps, draining pool liquidity at oracle-derived prices. The allowlist — the sole access-control mechanism for the pool — is rendered inoperative.

---

### Likelihood Explanation

**High.** `MetricOmmSimpleRouter` is the primary user-facing swap entry point. Any pool admin who wants their allowlisted users to be able to use the router must allowlist the router address. This is the natural, expected configuration. The documentation for neither `SwapAllowlistExtension` nor `MetricOmmSimpleRouter` warns that allowlisting the router grants universal access. The misconfiguration is the default path for any operator who deploys both components together. [5](#0-4) [6](#0-5) 

---

### Recommendation

The router must propagate the originating user's identity to the extension layer. Two complementary fixes:

1. **Router side**: encode the original `msg.sender` into `extensionData` before forwarding to the pool, so extensions can decode and verify it.

2. **Extension side**: `SwapAllowlistExtension.beforeSwap` should decode the original caller from `extensionData` (with a trusted-router flag or a signed payload) and check that address against the allowlist instead of the raw `sender` argument.

Alternatively, the pool's `swap` interface could be extended with an explicit `originator` field that the router populates with `msg.sender`, and the extension checks `originator` rather than `sender`.

---

### Proof of Concept

```
Setup
─────
1. Deploy a pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — intended to let allowlisted users reach the pool via the router.
3. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowed
   Pool admin does NOT allowlist bob.

Attack
──────
4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(
       pool=pool, recipient=bob, zeroForOne=true, amountIn=X, ...
   ).
5. Router calls pool.swap(...); pool's msg.sender = router.
6. Pool calls _beforeSwap(sender=router, ...).
7. Extension checks allowedSwapper[pool][router] → true → passes.
8. Bob's swap executes at oracle price; allowlist is bypassed.

Verification
────────────
9. Direct call: bob calls pool.swap(...) directly.
10. Extension checks allowedSwapper[pool][bob] → false → reverts NotAllowedToSwap.
    Bob is blocked on the direct path but not through the router.
```

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-13)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L15-18)
```text
/// @title MetricOmmSimpleRouter
/// @notice Exact-input and exact-output swaps through one or more MetricOmm pools.
/// @dev Expected callback pool, payer, token, and swap mode are stored in transient storage at entry.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }
```
