### Title
`SwapAllowlistExtension.beforeSwap` checks the direct pool caller (`sender = router`) instead of the end user, allowing any user to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is designed to gate `swap` access per pool by individual swapper address. However, the `sender` argument it checks is `msg.sender` of `pool.swap()`, which is the **router contract** when users trade through `MetricOmmSimpleRouter`. Because the router is a single shared address, any pool admin who allowlists the router (a natural step to enable router-mediated trading for their allowlisted users) inadvertently opens the gate to every user on the network. Conversely, if the router is not allowlisted, allowlisted users are silently blocked from using the standard router interface.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// metric-core/contracts/MetricOmmPool.sol  line 230-240
_beforeSwap(
  msg.sender,   // ← direct caller of pool.swap()
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

`SwapAllowlistExtension.beforeSwap` then checks that `sender` value against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol  line 31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol  line 71-80
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

`msg.sender` inside `pool.swap()` is the **router**, so `sender` delivered to `beforeSwap` is always the router address — regardless of which end user initiated the transaction. The router does not inject the original caller's identity into `extensionData` or any other field visible to the extension.

This creates two mutually exclusive failure modes for any pool that configures `SwapAllowlistExtension`:

| Router allowlisted? | Effect |
|---|---|
| **Yes** (`allowedSwapper[pool][router] = true`) | Every user on the network can bypass the per-user allowlist by routing through the router |
| **No** | Allowlisted users cannot use the router at all; they must call `pool.swap()` directly |

There is no configuration that simultaneously allows specific allowlisted users to use the router while blocking others.

---

### Impact Explanation

**Direct loss / broken invariant**: A pool deploying `SwapAllowlistExtension` to enforce KYC, compliance, or access-control restrictions cannot achieve its intended security property when the router is involved. If the pool admin allowlists the router (the natural step to enable router-mediated trading), any unprivileged user can bypass the allowlist and trade in a restricted pool. This constitutes an admin-boundary break: an access-control guard configured by the pool admin is bypassed by an unprivileged path (calling through the public router). It also constitutes broken core pool functionality: allowlisted users are blocked from the standard swap interface if the router is not allowlisted.

---

### Likelihood Explanation

Any pool that (a) configures `SwapAllowlistExtension` and (b) expects users to trade through `MetricOmmSimpleRouter` is affected. The pool admin has no correct configuration option: allowlisting the router opens the gate to everyone; not allowlisting it silently breaks router access for legitimate users. The trigger is an unprivileged user calling the public router — no special role or token is required.

---

### Recommendation

The extension must check the **end user's identity**, not the direct caller of `pool.swap()`. Two viable approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool. The extension decodes and verifies it. This requires a trust assumption that the router is the only entry point, which must be enforced separately.

2. **Check `sender` only for direct pool calls; require the router to be a verified intermediary that attests the real user**: The extension reads a router-attested identity from a well-known slot in `extensionData`, and the router is responsible for injecting `abi.encode(realUser)` before calling the pool.

The simplest safe fix is to have the router always prepend the originating `msg.sender` to `extensionData`, and have `SwapAllowlistExtension` decode and check that value when `sender` is a known router address.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `beforeSwap` order.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — a natural step to enable router usage.
3. Attacker (address not in allowlist) calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
4. Inside `pool.swap()`, `msg.sender = router`, so `sender = router` is passed to `beforeSwap`.
5. `allowedSwapper[pool][router] == true` → check passes → attacker's swap executes in the restricted pool.
6. The per-user allowlist is fully bypassed without any privileged action by the attacker.

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
