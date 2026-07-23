### Title
`SwapAllowlistExtension` swap guard bypassed by any user routing through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the router address, not the user. If the pool admin allowlists the router (the natural step to enable router-mediated swaps), every unprivileged user can bypass the allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check as follows: [1](#0-0) 

```solidity
function beforeSwap(address sender, ...)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

Here `msg.sender` is the pool (the extension is called by the pool), and `sender` is the value the pool passes — which is `msg.sender` of the original `pool.swap()` call: [2](#0-1) 

The pool passes `msg.sender` as `sender` to `_beforeSwap`, which forwards it verbatim to the extension: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly: [4](#0-3) 

At that point `msg.sender` of `pool.swap()` is the **router**, not the user. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Attack path:**

1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses.
2. Admin allowlists the router so that allowlisted users can swap via the router (the normal operational step).
3. Non-allowlisted user Alice calls `router.exactInputSingle(pool, ...)`.
4. Router calls `pool.swap(...)` — pool sees `msg.sender = router`.
5. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. Alice swaps successfully despite never being on the allowlist.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` because all of them call `pool.swap()` with the router as `msg.sender`: [5](#0-4) 

---

### Impact Explanation

The `SwapAllowlistExtension` is the production mechanism for restricting which addresses may trade on a pool. Once the router is allowlisted (required for any router-mediated swap to work), the allowlist provides zero protection: any address in the world can swap by routing through the public router. This breaks the core pool functionality the extension is designed to enforce and can expose restricted pools (e.g., KYC-gated, institutional, or pre-launch pools) to unrestricted public trading, with direct fund-flow consequences for LPs who deposited under the assumption of a controlled participant set.

---

### Likelihood Explanation

The trigger requires no special privilege. Any user with tokens can call the public router. The pool admin allowlisting the router is not an adversarial action — it is the expected operational step to enable normal router-mediated swaps. The bypass is therefore reachable in every realistic deployment of a swap-allowlisted pool that also supports router access.

---

### Recommendation

The extension must gate the **original user**, not the immediate caller of `pool.swap()`. Two options:

1. **Check `tx.origin` as a fallback** — fragile and generally discouraged.
2. **Require the router to forward the original user's identity in `extensionData`** and have the extension decode and verify it. The router would encode `msg.sender` into `extensionData` before calling `pool.swap()`, and the extension would decode and check that address against the allowlist. This requires a coordinated change to the router and the extension.
3. **Do not allowlist the router; instead allowlist individual users** — operationally burdensome but closes the gap without code changes.

The cleanest fix is option 2: the router encodes the originating user in `extensionData`, and `SwapAllowlistExtension.beforeSwap` decodes and checks that address when `sender` is a known router.

---

### Proof of Concept

```
Setup:
  - Pool P with SwapAllowlistExtension E.
  - Admin allowlists router R: allowedSwapper[P][R] = true.
  - Alice (address A) is NOT allowlisted: allowedSwapper[P][A] = false.

Direct swap attempt (blocked):
  Alice calls P.swap(...) directly.
  → _beforeSwap(sender=A, ...) → allowedSwapper[P][A] = false → revert NotAllowedToSwap ✓

Router bypass (succeeds):
  Alice calls R.exactInputSingle({pool: P, ...}).
  → R calls P.swap(...).
  → _beforeSwap(sender=R, ...) → allowedSwapper[P][R] = true → swap executes ✗

Result: Alice swaps on a pool she is not authorized to access.
``` [1](#0-0) [6](#0-5) [3](#0-2)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
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
```
