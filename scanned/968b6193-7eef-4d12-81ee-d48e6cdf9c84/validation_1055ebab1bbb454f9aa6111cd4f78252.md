### Title
SwapAllowlistExtension Gates Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks whether the **router** is allowlisted — not the actual user. A pool admin who allowlists the router to enable router-mediated swaps for their curated users inadvertently opens the pool to every user, completely defeating the allowlist.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension caller) and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`:

```solidity
function _beforeSwap(address sender, ...) internal {
    _callExtensionsInOrder(
        BEFORE_SWAP_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
    );
}
```

The pool sets `sender = msg.sender` of `pool.swap()`:

```solidity
_beforeSwap(msg.sender, recipient, zeroForOne, ...);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`), the router calls `pool.swap()` directly:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

The original user's address is stored only in transient storage for the payment callback — it is **never forwarded to the extension**. The extension therefore sees `sender = router address`.

**Contrast with `DepositAllowlistExtension`**, which correctly checks `owner` (the economic beneficiary of the position), not `sender` (the caller). The swap allowlist checks the wrong actor.

**Attack path:**

1. Pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict swaps to a whitelist of trusted counterparties (e.g., to exclude MEV bots or enforce KYC).
2. Pool admin allowlists specific user addresses via `setAllowedToSwap(pool, userA, true)`.
3. Pool admin also calls `setAllowedToSwap(pool, router, true)` so that their allowlisted users can use the router (a natural operational step).
4. Any unpermissioned user calls `router.exactInputSingle(...)` targeting the curated pool.
5. The router calls `pool.swap()` → `_beforeSwap(router, ...)` → extension checks `allowedSwapper[pool][router]` → **passes**, because the router is allowlisted.
6. The unauthorized user successfully swaps against the curated pool's LP liquidity.

The pool admin has no way to distinguish "router called on behalf of an allowlisted user" from "router called on behalf of an arbitrary user" because the extension only receives the router address.

---

### Impact Explanation

The allowlist is the sole access-control gate on a curated pool's swap path. Once bypassed, any user can trade against LP liquidity that was intentionally restricted. LPs in curated pools (e.g., institutional pools, KYC-gated pools, pools excluding known MEV actors) suffer direct principal loss because they are now exposed to adversarial flow they explicitly opted out of. The oracle-anchored pricing model does not protect LPs from toxic order flow — the allowlist is the only defense.

---

### Likelihood Explanation

The bypass requires the pool admin to allowlist the router, which is the natural and expected operational step for any curated pool that wants to support standard periphery usage. The pool admin has no reason to suspect this opens the pool to all users. The router is a public, permissionless contract. Any user who discovers the allowlisted router address (which is publicly deployed and verifiable) can immediately exploit the bypass with zero additional privilege.

---

### Recommendation

The extension must gate the **economic actor**, not the intermediary. Two viable approaches:

1. **Check `recipient` as a proxy for the user** — only valid if the pool enforces `recipient == sender` for direct swaps, which it does not.
2. **Require the actual user address in `extensionData`** — the router encodes the original `msg.sender` into `extensionData`, and the extension decodes and checks it. This requires a coordinated change to the router and extension.
3. **Preferred: mirror the deposit allowlist pattern** — pass the original user address as a dedicated parameter (analogous to `owner` in `beforeAddLiquidity`) so the extension always receives the economically relevant actor regardless of the call path.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][alice] = true   // alice is the intended user
  - allowedSwapper[pool][router] = true  // admin enables router for alice

Attack:
  - bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - router calls pool.swap(recipient, ...)  // msg.sender = router
  - pool calls extension.beforeSwap(router, ...)
  - extension checks allowedSwapper[pool][router] == true  → passes
  - bob's swap executes against the curated pool's LP liquidity

Result:
  - bob, an unauthorized user, successfully swaps in a pool designed to exclude him
  - LPs are exposed to adversarial flow they explicitly opted out of
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
