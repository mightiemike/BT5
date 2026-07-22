### Title
`SwapAllowlistExtension` checks the router address as `sender`, not the originating user — any unprivileged caller can bypass a curated pool's swap allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router address, not the originating user. A pool admin who allowlists the router (to enable router-mediated swaps for their permitted users) inadvertently grants every unprivileged caller the ability to bypass the per-user allowlist entirely.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that exact value against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(...)` directly, making the router the `msg.sender` of that call:

```solidity
// MetricOmmSimpleRouter.sol:72-80
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

So when any user calls the router, the extension sees `sender = router_address`. If the pool admin has allowlisted the router (the natural step to enable router-mediated swaps for their permitted users), the check `allowedSwapper[pool][router] == true` passes for **every** caller regardless of their individual allowlist status.

The pool admin faces an impossible choice:
- **Do not allowlist the router**: even allowlisted users cannot use the router (broken functionality).
- **Allowlist the router**: every unprivileged user can bypass the per-user allowlist (security bypass).

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC-verified counterparties, institutional partners, or protocol-controlled addresses) loses that restriction entirely once the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` and trade against the pool's LP positions at oracle-derived prices. LP providers who deposited under the assumption that only vetted counterparties could trade against them are exposed to unrestricted adverse selection and unintended counterparty risk. This constitutes a broken core pool functionality and an admin-boundary break where the configured allowlist guard is bypassed by an unprivileged path.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool admin who wants their allowlisted users to be able to use the router (the standard UX path) must allowlist the router address. This is the expected operational pattern, making the bypass condition highly likely to be triggered in production deployments of curated pools.

---

### Recommendation

The `SwapAllowlistExtension` must gate by the **originating user**, not the immediate caller of `pool.swap()`. Two complementary fixes:

1. **Pass the originating user through the router**: `MetricOmmSimpleRouter` should forward `msg.sender` as a verified field in `extensionData` (signed or verified via transient storage), and `SwapAllowlistExtension` should read and check that value instead of `sender`.

2. **Alternatively, check `sender` against the allowlist and also check the router's own allowlist separately**: the extension could maintain a separate `allowedRouter` mapping and, when `sender` is a known router, additionally verify the originating user from the router's transient context.

The simplest correct fix is to have the router store the originating `msg.sender` in transient storage and expose it via a standard interface that the extension can query, so the allowlist check always operates on the economic actor, not the intermediary.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as the `beforeSwap` hook.
2. Pool admin calls `swapExtension.setAllowedToSwap(pool, alice, true)` — only Alice is permitted.
3. Pool admin calls `swapExtension.setAllowedToSwap(pool, address(router), true)` — router is allowlisted so Alice can use it.
4. Bob (not allowlisted) calls:
   ```solidity
   router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
       pool: address(pool),
       tokenIn: token0,
       ...
       extensionData: ""
   }));
   ```
5. Router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. Pool calls `extension.beforeSwap(router, recipient, ...)`.
7. Extension evaluates `allowedSwapper[pool][router] == true` → **passes**.
8. Bob's swap executes successfully against the curated pool, bypassing the per-user allowlist.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
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
