### Title
`SwapAllowlistExtension` gates the router address instead of the actual end-user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument supplied by the pool, which equals `msg.sender` of the `pool.swap()` call. When a swap is routed through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the actual end-user. If the pool admin allowlists the router (a necessary step to permit any router-mediated swap on a curated pool), every unprivileged user can bypass the allowlist by routing through the router.

---

### Finding Description

**Root cause — wrong actor checked**

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to every extension hook:

```solidity
// MetricOmmPool.sol – swap()
_beforeSwap(
    msg.sender,   // ← this is the router when called via MetricOmmSimpleRouter
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then gates on that value:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool; `sender` is the router when the call originates from `MetricOmmSimpleRouter.exactInputSingle` / `exactInput`. The actual end-user who called the router is stored only in the router's transient payment context and is never forwarded to the pool or the extension.

**Bypass path**

1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict trading to specific addresses.
2. Admin allowlists the router so that their approved users can trade via the router: `swapExtension.setAllowedToSwap(pool, router, true)`.
3. Any unprivileged user calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
4. The router calls `pool.swap(recipient, ...)` — `msg.sender` of that call is the router.
5. `_beforeSwap(router, ...)` is dispatched; the extension evaluates `allowedSwapper[pool][router]` = `true` → passes.
6. The swap executes for the unprivileged user despite them not being on the allowlist.

The admin has no way to selectively permit router-mediated swaps for specific users without simultaneously opening the pool to every user who can reach the router.

---

### Impact Explanation

Any user can trade on a pool that the admin intended to restrict to a curated set of swappers. This breaks the core access-control invariant of the `SwapAllowlistExtension` and constitutes an admin-boundary break: an unprivileged path (the public router) bypasses a configured guard. Depending on the pool's purpose (e.g., private LP arrangements, regulatory-gated pools), this can result in unauthorized extraction of LP value or violation of the pool's intended trading policy.

---

### Likelihood Explanation

The trigger condition — the router being allowlisted — is a natural and expected configuration step. Any pool admin who wants their approved users to be able to trade via the standard periphery router must allowlist the router. The moment they do, the bypass is universally available to all users. The router is a public, permissionless contract, so no further privilege is required.

---

### Recommendation

The extension must gate on the actual end-user, not the immediate caller of `pool.swap()`. Two approaches:

1. **Pass the real user in `extensionData`**: The router encodes the original `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a convention between the router and the extension.
2. **Check `recipient` instead of `sender`**: For swap allowlists, gating on the output recipient (which the user controls) is closer to the intended policy, though it still does not perfectly identify the payer.
3. **Document the limitation**: If the design intentionally gates on the immediate caller, the documentation and admin tooling must make clear that allowlisting the router opens the pool to all users, and provide a per-user router wrapper pattern instead.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only alice is intended to swap
swapExtension.setAllowedToSwap(pool, alice, true);
// Admin also allowlists the router so alice can use it
swapExtension.setAllowedToSwap(pool, address(router), true);

// Attack: bob (not allowlisted) routes through the public router
vm.startPrank(bob);
token1.approve(address(router), type(uint256).max);
router.exactInputSingle(ExactInputSingleParams({
    pool:            pool,
    tokenIn:         address(token1),
    recipient:       bob,
    zeroForOne:      false,
    amountIn:        1000,
    amountOutMinimum: 0,
    priceLimitX64:   type(uint128).max,
    deadline:        block.timestamp,
    extensionData:   ""
}));
// ✓ swap succeeds — bob bypassed the allowlist
```

The pool's `_beforeSwap` receives `sender = address(router)`, which is allowlisted, so the extension passes. Bob's identity is never checked. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
