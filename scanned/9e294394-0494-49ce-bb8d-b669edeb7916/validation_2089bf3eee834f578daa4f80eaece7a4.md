### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against a per-pool allowlist. The pool passes `msg.sender` as `sender`, which is the **router contract address** when a swap is routed through `MetricOmmSimpleRouter`. Because the extension sees the router—not the originating user—as the swapper, any pool admin who allowlists the router to support legitimate router-mediated swaps simultaneously opens the gate to every unprivileged user on the network.

---

### Finding Description

**Call chain for a direct swap (correct):**

```
user → pool.swap()
  pool: msg.sender = user
  pool: _beforeSwap(msg.sender=user, ...)
  extension: beforeSwap(sender=user, ...)
  check: allowedSwapper[pool][user]  ← correct actor
```

**Call chain for a router-mediated swap (broken):**

```
user → router.exactInputSingle(params)
  router → pool.swap(params.recipient, ...)
    pool: msg.sender = router
    pool: _beforeSwap(msg.sender=router, ...)
    extension: beforeSwap(sender=router, ...)
    check: allowedSwapper[pool][router]  ← wrong actor
```

In `MetricOmmPool.swap`, the pool unconditionally passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool passed—the router address when the router is the immediate caller: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly with no mechanism to forward the original `msg.sender`: [4](#0-3) 

The same pattern applies to `exactInput` (all hops), `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` faces an inescapable dilemma:

| Router allowlist state | Allowlisted users via router | Non-allowlisted users via router |
|---|---|---|
| Router **not** allowlisted | **Blocked** (broken UX) | Blocked |
| Router **allowlisted** | Allowed | **Allowed** (bypass) |

If the admin allowlists the router so that legitimate users can access the pool through the standard periphery path, every unprivileged address on the network can bypass the allowlist by calling `router.exactInputSingle` (or any other router entry point). The allowlist is rendered completely ineffective. This constitutes a **broken core pool functionality** and a **curation failure** with direct fund-impact consequences: the pool receives swaps from actors the admin explicitly intended to exclude, which can drain LP assets at oracle-derived prices the pool was not designed to expose to arbitrary counterparties.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the documented, production periphery entry point for swaps.
- Any pool admin who wants allowlisted users to be able to use the router must allowlist the router address, which is the natural operational setup.
- No special preconditions, flash loans, or privileged access are required. Any EOA can call `router.exactInputSingle` with a valid `extensionData` payload.
- The bypass is reachable on every swap direction and every router function (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`).

---

### Recommendation

The extension must check the **original user**, not the immediate pool caller. Two sound approaches:

1. **Pass the original sender through the router.** Add a `sender` parameter to the pool's `swap` signature (or a separate authenticated forwarding mechanism) so the router can attest the originating user. The extension then checks that attested address.

2. **Check `tx.origin` as a fallback.** When `sender` is a known router, fall back to `tx.origin`. This is fragile and generally discouraged but is a short-term mitigation.

3. **Preferred: gate at the router level.** Move allowlist enforcement into the router itself (e.g., a router-level allowlist that the pool admin controls), so the check always operates on `msg.sender` at the outermost entry point.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `alice` is allowlisted.
// pool admin does NOT allowlist the router initially.
// alice cannot use the router (router blocked).
// Admin then allowlists the router so alice can use it.
// Now bob (not allowlisted) calls:

router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool:            address(allowlistedPool),
    tokenIn:         address(token0),
    tokenOut:        address(token1),
    zeroForOne:      true,
    amountIn:        1_000,
    amountOutMinimum: 0,
    recipient:       bob,
    deadline:        block.timestamp + 1,
    priceLimitX64:   0,
    extensionData:   ""
}));
// Succeeds: extension sees sender=router (allowlisted), not bob (not allowlisted).
// Bob receives token1 from a pool he was explicitly excluded from.
```

The pool's `_beforeSwap` receives `sender = address(router)`. [6](#0-5) 

The extension evaluates `allowedSwapper[pool][router]` = `true`, and the swap proceeds. [7](#0-6)

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
