### Title
`SwapAllowlistExtension` gates the router address instead of the real swapper, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so `sender` is the router address — not the actual user. If the pool admin allowlists the router (the only way to permit router-mediated swaps), every non-allowlisted user can bypass the gate by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it to every configured extension: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` is on the allowlist, using `msg.sender` (the calling pool) as the mapping key: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [3](#0-2) 

At that point `msg.sender` to the pool is the router, so `sender` delivered to the extension is the router address. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

A pool admin who wants router-mediated swaps to work for their allowlisted users must add the router to the allowlist. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for **every** caller of the router, regardless of whether that caller is on the allowlist. The guard is completely neutralised.

The same identity collapse occurs for every multi-hop step in `exactInput` where intermediate hops use `address(this)` (the router) as the payer: [4](#0-3) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC-verified addresses, whitelisted market makers, or protocol-internal actors) loses that restriction entirely once the router is allowlisted. Any unprivileged user can swap against the pool's liquidity by calling the public router. LP funds deposited under the assumption of a restricted pool are exposed to unrestricted trading, which can result in direct loss of LP principal through adverse price execution or volume the pool was not designed to absorb.

---

### Likelihood Explanation

Likelihood is high. The `MetricOmmSimpleRouter` is a public, permissionless periphery contract. Any user can call `exactInputSingle` or `exactInput`. The pool admin is forced into a binary choice: either allowlist the router (breaking the guard for all users) or block the router (breaking router-mediated swaps for legitimate allowlisted users). There is no mechanism in the current design to thread the actual user identity through the router into the extension check.

---

### Recommendation

The `SwapAllowlistExtension` should gate the **economically relevant actor**, not the intermediary. Two complementary fixes:

1. **Short term:** In `SwapAllowlistExtension.beforeSwap`, check `recipient` (the address receiving output tokens) in addition to or instead of `sender` when `sender` is a known router, or require callers to pass the real user address in `extensionData` and verify it with a signature.

2. **Long term:** Introduce a trusted-forwarder pattern in the router: the router appends `msg.sender` to `extensionData` and the extension reads and verifies it, similar to ERC-2771. Alternatively, the pool's `swap` interface could accept an explicit `swapper` argument that the router populates with `msg.sender` before forwarding to the pool.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the only allowed swapper
  - Pool admin calls setAllowedToSwap(pool, router, true)  // required for router-mediated swaps

Attack:
  - Bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient=bob, ...)
  - Pool calls extension.beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] == true  → passes
  - Bob's swap executes against the restricted pool's liquidity

Result:
  - Bob, who is not on the allowlist, successfully swaps against a pool
    that was intended to be restricted to alice only.
  - LP funds are exposed to an unauthorized counterparty.
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
