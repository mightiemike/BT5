### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `swap` is called with `msg.sender = router`, so `sender` forwarded to the extension is the **router address**, not the original user. If the pool admin allowlists the router to enable router-mediated swaps, every unprivileged user can bypass the allowlist by routing through the router.

---

### Finding Description

**Call chain for a router-mediated swap:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle(params)`.
2. Router calls `IMetricOmmPoolActions(params.pool).swap(recipient, zeroForOne, ..., extensionData)` — here `msg.sender` inside the pool is the **router**.
3. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, recipient, ...)`, forwarding `msg.sender = router` as `sender`.
4. `ExtensionCalling._beforeSwap` encodes `sender = router` and dispatches to `SwapAllowlistExtension.beforeSwap`.
5. The extension evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

where `msg.sender` is the pool and `sender` is the **router**, not the original user. [1](#0-0) 

The pool passes `msg.sender` (the router) as `sender` to `_beforeSwap`: [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [3](#0-2) 

This creates an irreconcilable dilemma for the pool admin:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all — broken core functionality |
| **Allowlist the router** | Every user, including non-allowlisted ones, can bypass the allowlist by routing through the router |

The second branch is the exploitable path: once the admin allowlists the router (the natural action to let their curated users trade via the standard periphery), the allowlist is completely defeated for all users.

The `DepositAllowlistExtension` does not share this flaw because it checks `owner` (the position owner explicitly supplied by the caller), not `sender` (the intermediary contract). [4](#0-3) 

---

### Impact Explanation

A pool deployer uses `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, whitelisted market makers). Once the admin allowlists the router so that legitimate users can trade via the standard periphery, any unprivileged address can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` on the router and trade against the pool without restriction. The allowlist provides zero protection. This is a direct loss-of-curation-policy impact on every pool relying on `SwapAllowlistExtension` with router support enabled, and constitutes broken core pool functionality (the guard silently fails open).

---

### Likelihood Explanation

The trigger requires no special privilege. Any user with the pool address and the router address can exploit this. The only precondition is that the pool admin has allowlisted the router — a natural and expected administrative action for any pool that intends to support the standard periphery. The router is a factory-validated contract (`_requireFactoryPool` is called before every swap context is set), so it is a trusted, publicly known address that admins are expected to allowlist. [5](#0-4) 

---

### Recommendation

The extension must gate the **original user**, not the intermediary. Two viable approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` (the original user) into `extensionData` for the extension to decode and check. The extension verifies the encoded address against the allowlist instead of `sender`.

2. **Check `sender` only when `sender` is not a known router**: The factory could expose a registry of trusted periphery contracts; the extension falls back to checking `extensionData`-encoded identity when `sender` is a registered router.

The simplest correct fix is option 1: the router always prepends the original caller to `extensionData`, and `SwapAllowlistExtension.beforeSwap` decodes and checks that address when `sender` is a registered periphery contract.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension.
  - Admin allowlists alice (alice is the only permitted swapper).
  - Admin allowlists the router so alice can use the standard periphery.

Attack (executed by bob, a non-allowlisted address):
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({
         pool: pool,
         recipient: bob,
         zeroForOne: true,
         amountIn: X,
         ...
     });
  2. Router calls pool.swap(bob, true, X, ..., extensionData)
     → msg.sender inside pool = router
  3. Pool calls _beforeSwap(router, bob, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true (admin allowlisted router)
  5. Swap executes successfully for bob despite bob not being on the allowlist.

Result: allowlist is fully bypassed; bob trades on a curated pool without authorization.
``` [6](#0-5) [1](#0-0)

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

**File:** metric-periphery/contracts/base/MetricOmmSwapRouterBase.sol (L87-89)
```text
  function _requireFactoryPool(address pool) internal view {
    if (!FACTORY.isPool(pool)) revert IMetricOmmSimpleRouter.InvalidPool(pool);
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
