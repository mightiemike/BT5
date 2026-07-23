### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, making the allowlist either fully bypassable or broken for router-mediated swaps — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router**, not the actual user. This creates a direct contradiction: the extension is designed to gate specific user addresses, but the identity it actually checks is the router's address. The pool admin is forced into an impossible choice — either allowlist the router (granting every user on-chain access to the curated pool) or leave the router un-allowlisted (breaking the supported periphery path for every legitimately allowlisted user).

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then gates on that `sender`:

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` — so `msg.sender` of `pool.swap()` is the **router contract**, not the user. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

Two mutually exclusive failure modes result:

**Mode A — allowlist bypass:** The pool admin allowlists the router address so that legitimately allowlisted users can trade through the supported periphery. Because the router is a public, permissionless contract, every address on-chain can now call `exactInputSingle` and pass the extension check. The per-user allowlist is completely nullified.

**Mode B — broken periphery:** The pool admin does not allowlist the router. Every call through `MetricOmmSimpleRouter` reverts with `NotAllowedToSwap`, even for users whose addresses are individually allowlisted. The supported swap path is broken for all allowlisted users.

There is no configuration that simultaneously (a) allows allowlisted users to trade through the router and (b) blocks non-allowlisted users from doing the same.

---

### Impact Explanation

**Critical/High.** The `SwapAllowlistExtension` is the primary access-control mechanism for curated pools. Under Mode A, any unprivileged address can trade on a pool that was explicitly restricted to a whitelist, directly violating the pool's curation invariant and potentially draining LP value through unrestricted arbitrage or front-running. Under Mode B, the supported periphery path is permanently broken for all allowlisted users, constituting broken core pool functionality. Both outcomes are fund-impacting and match the Metric OMM Allowed Impact Gate (allowlist bypass / broken core swap flow).

---

### Likelihood Explanation

**High.** The `MetricOmmSimpleRouter` is the primary user-facing swap entry point. Any pool that deploys `SwapAllowlistExtension` and expects users to trade through the router will encounter this contradiction on the very first router-mediated swap. No special preconditions, privileged access, or exotic token behavior is required — a standard `exactInputSingle` call is sufficient.

---

### Recommendation

The extension must gate the **economically relevant actor**, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass the real user through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between the router and the extension.

2. **Check `recipient` instead of `sender`:** For single-hop swaps the recipient is typically the user. This is imprecise for multi-hop paths where intermediate recipients are the router itself.

3. **Dedicated router allowlist entry:** Document that the router address must never be allowlisted and that allowlisted users must call `pool.swap()` directly. This is operationally fragile and breaks the periphery UX.

The cleanest fix is approach 1: the router appends `abi.encode(msg.sender)` to `extensionData`, and `SwapAllowlistExtension.beforeSwap` decodes and checks that address when `sender` is a known router.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — required so Alice can use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(bob, ...)` with `msg.sender = router`.
6. `beforeSwap` receives `sender = router`; checks `allowedSwapper[pool][router]` → `true`.
7. Bob's swap executes successfully — the allowlist is bypassed.

Alternatively, if step 3 is omitted, Alice's call through the router at step 4 reverts with `NotAllowedToSwap` even though she is individually allowlisted, breaking the supported periphery path.

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
