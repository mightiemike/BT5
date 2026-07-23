### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so `sender` is the router address — not the actual user. If the pool admin allowlists the router (the only way to permit router-mediated swaps), every user on the network can bypass the per-user allowlist by calling the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` — i.e., `allowedSwapper[pool][caller_of_pool_swap]`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` to the pool: [4](#0-3) 

The same applies to `exactInput` (multi-hop) and `exactOutputSingle`: [5](#0-4) 

**Consequence:** The extension sees `sender = router address` for every router-mediated swap. The pool admin faces an impossible choice:

- **Do not allowlist the router** → allowlisted users cannot use the router at all (blocked because the router address is not in the allowlist).
- **Allowlist the router** → the allowlist is completely bypassed; any user can call the router and trade on the curated pool.

There is no configuration that simultaneously permits router-mediated swaps for allowlisted users and blocks non-allowlisted users.

---

### Impact Explanation

A pool deployed with `SwapAllowlistExtension` is a curated pool intended to restrict trading to specific addresses. Once the router is allowlisted (the only way to support the standard periphery path), any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps on the pool. The allowlist provides zero protection against router-mediated access. This constitutes a direct broken-core-functionality finding: the configured guard fails open for every user who routes through the supported public periphery contract.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface documented and deployed alongside the protocol. Any user who discovers that the router is allowlisted on a curated pool can immediately exploit this. No special privileges, flash loans, or multi-step setup are required — a single `exactInputSingle` call suffices.

---

### Recommendation

The `SwapAllowlistExtension` must check the economically relevant actor, not the immediate caller of `pool.swap`. Two sound approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires the router to be trusted to supply honest data, which is acceptable since the router is a known, audited contract.

2. **Check `recipient` instead of `sender`**: For swap allowlists the recipient is often the intended gated actor. However, this changes semantics for operator patterns.

3. **Dedicated router-aware allowlist**: Extend the extension to recognize the router as a transparent forwarder and read the original user from a router-specific field in `extensionData`.

The simplest correct fix is option 1: the router appends `abi.encode(msg.sender)` to `extensionData`, and the extension decodes and checks that value when `sender` is a known router address.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension.
  - Pool admin allowlists only `alice` via setAllowedToSwap(pool, alice, true).
  - Pool admin also allowlists the router: setAllowedToSwap(pool, router, true)
    (required so alice can use the standard periphery path).

Attack (executed by `eve`, a non-allowlisted address):
  1. eve calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
  2. Router calls pool.swap(recipient, ...) — pool's msg.sender = router.
  3. Pool calls _beforeSwap(router, ...).
  4. SwapAllowlistExtension.beforeSwap receives sender = router.
  5. Check: allowedSwapper[pool][router] == true  →  passes.
  6. Swap executes. eve receives output tokens.

Result: eve, a non-allowlisted address, successfully swaps on a curated pool.
The allowlist is completely bypassed through the supported public router path.
```

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
