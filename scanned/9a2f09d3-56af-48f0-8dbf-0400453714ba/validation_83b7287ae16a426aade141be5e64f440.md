### Title
`SwapAllowlistExtension` gates the router address instead of the end user, allowing any unprivileged user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument (which equals `msg.sender` of `pool.swap()`) against the per-pool allowlist. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the end user. If the pool admin allowlists the router to enable router-mediated swaps, every unprivileged user can bypass the allowlist entirely by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` encodes that value and calls the extension:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)   // sender = msg.sender of pool.swap()
)
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the allowlist:

```solidity
function beforeSwap(address sender, ...)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool; `sender` is whoever called `pool.swap()`.

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap()` directly:

```solidity
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

The router is `msg.sender` of that call, so `sender` arriving at the extension is the **router address**, not the end user. The extension has no access to the original caller of `router.exactInputSingle`.

**Broken invariant:** For any pool whose admin allowlists the router (the only way to permit router-mediated swaps for legitimate users), the allowlist check degenerates to "is the router allowlisted?" — which is always true — and every unprivileged user can swap freely by routing through the public router.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to be a permissioned venue: only explicitly approved addresses may swap. Once the pool admin allowlists the router (a necessary step for any allowlisted user who wants to use the router), the guard is completely neutralised. Any address — including those the admin deliberately excluded — can call `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` and execute swaps against the pool's liquidity. This is a direct admin-boundary break: an unprivileged path (the public router) bypasses the pool admin's access-control gate, allowing unauthorised actors to interact with LP funds.

---

### Likelihood Explanation

The router is a public, permissionless contract. No special role, token balance, or prior state is required. Any user who observes that a pool has a swap allowlist and that the router is allowlisted can immediately exploit the bypass. The pool admin has no on-chain mechanism to distinguish "router called on behalf of an allowlisted user" from "router called on behalf of an arbitrary user," so the bypass is permanent for the lifetime of the pool configuration.

---

### Recommendation

The extension must gate the **economic actor** (the end user), not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Signature-based forwarding**: Require the end user to sign a permit that the router encodes into `extensionData`; the extension decodes and verifies the signer against the allowlist.

2. **Router-aware allowlist**: Extend the allowlist to a two-level mapping `allowedSwapper[pool][router][endUser]` and require the router to pass the end user's address in `extensionData`; the extension decodes it and checks the inner mapping.

3. **Direct-only policy**: Document that pools using `SwapAllowlistExtension` must not allowlist the router, and enforce this with a revert if the pool admin attempts to allowlist a known router address.

---

### Proof of Concept

```
1. Deploy MetricOmmPool with SwapAllowlistExtension as a beforeSwap hook.
2. Pool admin calls:
       extension.setAllowedToSwap(pool, router, true)   // allow router
       extension.setAllowedToSwap(pool, alice, true)    // allow alice directly
   Bob (non-allowlisted) is intentionally excluded.

3. Bob calls:
       router.exactInputSingle(ExactInputSingleParams{
           pool:      pool,
           recipient: bob,
           zeroForOne: true,
           amountIn:  X,
           ...
       })

4. Router calls pool.swap(bob, true, X, ...).
   pool.swap sees msg.sender = router → passes sender=router to _beforeSwap.
   Extension checks allowedSwapper[pool][router] → true → no revert.

5. Bob's swap executes against the pool's LP liquidity.
   The allowlist guard is completely bypassed.
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
