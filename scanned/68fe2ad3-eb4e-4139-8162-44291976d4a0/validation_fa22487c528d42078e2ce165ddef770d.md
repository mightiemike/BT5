### Title
`SwapAllowlistExtension` checks the router's identity instead of the actual swapper's identity, allowing any user to bypass the per-user allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which is the `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, the router becomes `sender`. If the pool admin allowlists the router address (a natural action to enable router-mediated swaps for their allowlisted users), every unpermissioned user can bypass the per-user allowlist by calling through the router, gaining unrestricted swap access to a pool that was intended to be restricted.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as the first argument and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the extension caller), and `sender` is whatever `MetricOmmPool.swap()` received as its own `msg.sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- the immediate caller of pool.swap()
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` with `msg.sender = router`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
    );
```

The pool then passes `sender = router` to the extension. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**Attack path:**

1. Pool admin deploys a pool with `SwapAllowlistExtension` as the `beforeSwap` hook, intending to restrict swaps to specific counterparties (e.g., KYC'd users Alice and Bob).
2. Admin allowlists Alice and Bob: `allowedSwapper[pool][alice] = true`, `allowedSwapper[pool][bob] = true`.
3. Admin also allowlists the router so Alice and Bob can use it: `allowedSwapper[pool][router] = true`.
4. Unauthorized user Charlie calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
5. Router calls `pool.swap(...)` with `msg.sender = router`.
6. Pool dispatches `_beforeSwap(sender=router, ...)`.
7. Extension checks `allowedSwapper[pool][router]` → `true` → passes.
8. Charlie's swap executes against LP funds in a pool that was intended to be restricted.

The same bypass applies to multi-hop `exactInput` and `exactOutput` paths, since every hop passes `sender = router` to the pool.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict access to specific counterparties (e.g., institutional or KYC-gated pools) can be fully bypassed by any user routing through `MetricOmmSimpleRouter`. The unauthorized user can execute swaps at oracle-derived prices against LP reserves, directly extracting LP principal. The allowlist guard — the only access-control mechanism on the swap path — fails to gate the economically relevant actor.

---

### Likelihood Explanation

The bypass requires the pool admin to allowlist the router address. This is a natural and expected action: if the admin wants their allowlisted users to benefit from the router's UX (slippage protection, multi-hop, deadline checks), they must allowlist the router. The admin is unlikely to realize that allowlisting the router grants swap access to every user of that public contract. The `MetricOmmSimpleRouter` is a public, permissionless contract, so once the router is allowlisted, the bypass is available to any address on-chain.

---

### Recommendation

The extension must gate the economically relevant actor — the human or contract that initiated the swap — not the intermediate router. Two approaches:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` (the original user) into `extensionData` before calling `pool.swap()`. The `SwapAllowlistExtension` decodes and checks that address. This requires the extension to trust the router, which can be enforced by also checking that `sender` (the pool's `msg.sender`) is a known trusted router.

2. **Check `sender` and reject known routers unless the original user is allowlisted**: The extension maintains a registry of trusted routers and, when `sender` is a router, requires the `extensionData` to carry a signed or encoded user identity.

The simplest safe default: do not allowlist the router address. Require allowlisted users to call `pool.swap()` directly. Document clearly that allowlisting the router grants access to all router users.

---

### Proof of Concept

```
Pool admin configures:
  allowedSwapper[pool][alice]  = true   // intended allowlist
  allowedSwapper[pool][router] = true   // to let alice use the router

Unauthorized user Charlie:
  MetricOmmSimpleRouter.exactInputSingle({
      pool:      restrictedPool,
      recipient: charlie,
      zeroForOne: true,
      amountIn:  1_000e18,
      ...
  })

Execution trace:
  router.exactInputSingle()
    → pool.swap(recipient=charlie, ...) [msg.sender = router]
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  (passes)
      → swap executes, LP funds transferred to charlie

Result: Charlie swaps 1_000e18 token0 for token1 from a pool
        that was intended to be restricted to alice and bob only.
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
