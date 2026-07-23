### Title
`SwapAllowlistExtension` gates the router address instead of the end-user, allowing any unprivileged swapper to bypass a curated pool's allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument it receives from the pool. The pool sets `sender = msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, not the end-user. The extension therefore checks whether the **router** is allowlisted, not the actual trader. If the router is allowlisted (required for any allowlisted user to trade through it), every non-allowlisted user can bypass the curated pool's swap gate by routing through the same public router.

---

### Finding Description

**Actor binding in the pool:**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to every before-swap extension hook:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

**Guard in the extension:**

`SwapAllowlistExtension.beforeSwap` uses that `sender` argument as the identity to check:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

Here `msg.sender` is the pool (correct pool key), and `sender` is the direct caller of `pool.swap()`.

**Router path:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of the pool:

```solidity
// MetricOmmSimpleRouter.sol
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

The pool therefore passes `sender = address(router)` to the extension. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Consequence:**

- If the router is **not** allowlisted: no user can swap through the router, even allowlisted ones — the guard is over-restrictive and breaks core swap functionality for the supported periphery path.
- If the router **is** allowlisted (the only way to let legitimate users use the router): every non-allowlisted address can bypass the curated pool's swap gate by calling `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` on the public router.

The same wrong-actor binding applies to the multi-hop `exactInput` path, where intermediate hops use `address(this)` (the router itself) as the payer, and the `exactOutput` recursive callback path also calls `pool.swap()` from within the router.

---

### Impact Explanation

**High.** A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of addresses (e.g., KYC'd counterparties, whitelisted market makers, or protocol-controlled addresses) loses that protection entirely for any user who routes through the public `MetricOmmSimpleRouter`. The non-allowlisted user receives real token output from the pool; the pool's LP providers are exposed to trades from actors the pool admin explicitly intended to exclude. This is a direct loss of the curation guarantee and constitutes broken core pool functionality for allowlisted pools.

---

### Likelihood Explanation

**High.** `MetricOmmSimpleRouter` is the primary supported public swap entrypoint documented in the periphery. Any user who discovers the allowlist can trivially route through the router. No privileged access, special token behavior, or unusual setup is required — a standard `exactInputSingle` call suffices.

---

### Recommendation

The extension must recover the true end-user identity rather than trusting the `sender` argument, which reflects only the direct caller of `pool.swap()`. Two sound approaches:

1. **Pass the originating user through `extensionData`**: require the router to encode `msg.sender` (the end-user) into `extensionData` and have the extension decode and verify it. The extension must also verify that `msg.sender` (the pool's caller, i.e., the router) is a trusted periphery contract before accepting the delegated identity.

2. **Allowlist at the router level**: gate `MetricOmmSimpleRouter` so that only allowlisted users can call `exactInputSingle` / `exactInput` / etc. for pools that require it. This requires the router to be pool-aware, which is architecturally heavier.

The simplest safe fix is option 1: the extension checks `allowedSwapper[pool][decodedUser]` only when `sender` is a known trusted router, and falls back to checking `sender` directly for direct pool calls.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed.
3. Admin calls `setAllowedToSwap(pool, address(router), true)` — necessary so Alice can use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient=bob, ...)` — pool's `msg.sender` is the router.
6. Pool calls `_beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob receives token output from the curated pool despite never being allowlisted.

If the admin does **not** allowlist the router (step 3 omitted), Alice's router calls also revert at step 7 (`allowedSwapper[pool][router]` → `false`), breaking the supported swap path for legitimate users. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
