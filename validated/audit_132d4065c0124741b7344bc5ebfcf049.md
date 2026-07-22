### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the original user. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted. This makes the swap allowlist guard structurally unenforceable for router-mediated swaps: if the router is allowlisted (required for any allowlisted user to use it), every user on-chain can bypass the allowlist by routing through the same router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes `sender` (the pool's direct caller) into the extension call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the pool's direct caller: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the pool's `msg.sender`. The original user's address is stored only in transient storage for the payment callback and is never forwarded to the pool or extensions: [4](#0-3) 

The result is a structural dilemma for any pool admin who configures `SwapAllowlistExtension`:

- **Router not allowlisted**: `allowedSwapper[pool][router] == false` → every router-mediated swap reverts with `NotAllowedToSwap`, including swaps from legitimately allowlisted users. Allowlisted users are forced to call `pool.swap()` directly.
- **Router allowlisted**: `allowedSwapper[pool][router] == true` → every user on-chain can call `router.exactInputSingle(pool, ...)` and the extension passes unconditionally, because it only sees the router address. The allowlist is fully bypassed.

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

The `SwapAllowlistExtension` is the protocol's mechanism for curated pools — pools where only specific addresses (e.g., designated market makers, KYC'd participants, or protocol-controlled bots) are permitted to trade. When the router is allowlisted, any unprivileged user can bypass this curation by routing through `MetricOmmSimpleRouter`. Consequences include:

- Unauthorized users trading in pools designed to be private or restricted.
- Adverse selection against LPs in curated pools if the allowlist was intended to exclude toxic flow.
- Complete nullification of the pool admin's access-control policy through a supported, public periphery path.

This matches the "admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" impact gate.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is a public, permissionless contract. Any user who discovers that a pool uses `SwapAllowlistExtension` and that the router is allowlisted can immediately exploit this by calling `router.exactInputSingle`. No special privileges, flash loans, or multi-step setup are required. The router is the standard, documented entry point for swaps, so pool admins are likely to allowlist it to support normal user flows.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the **economically relevant actor** — the address that initiated the trade and will receive or pay tokens — not the intermediate contract that called `pool.swap()`. Two approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`, and the extension decodes and verifies it. This requires the extension to trust that the router correctly reports the user, which introduces a trust assumption on the router.

2. **Check `recipient` instead of `sender`**: For exact-input swaps, the recipient is the address that receives output tokens. If the pool admin intends to gate who benefits from the swap, checking `recipient` is more semantically correct. However, `recipient` can also be a third party.

3. **Preferred — router-aware identity forwarding**: Redesign the extension interface to include an `originator` field that the router populates with `msg.sender` before calling the pool, and have the pool forward it as a distinct argument to extensions. This is the cleanest fix and mirrors how Uniswap v4 hooks handle the `hookData` pattern.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin allowlists userA: allowedSwapper[pool][userA] = true
  - Pool admin allowlists router: allowedSwapper[pool][router] = true
    (required so userA can use the router)

Attack (userB, not allowlisted):
  1. userB calls router.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, ...) — msg.sender in pool = router
  3. Pool calls extension.beforeSwap(router, ...) — sender = router
  4. Extension checks: allowedSwapper[pool][router] == true → passes
  5. userB's swap executes successfully in the curated pool

Result:
  - userB bypassed the swap allowlist entirely
  - Pool admin cannot prevent this without also blocking userA from using the router
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
