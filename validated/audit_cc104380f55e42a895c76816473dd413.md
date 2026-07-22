### Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the end user, allowing any caller to bypass the per-user allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension` is the production extension that gates pool swaps to an explicit per-pool allowlist. Its `beforeSwap` hook checks `allowedSwapper[msg.sender][sender]`, where `sender` is the direct caller of `MetricOmmPool.swap`. When a user routes through `MetricOmmSimpleRouter`, `sender` equals the router address, not the end user. If the pool admin allowlists the router (the natural action to let users access the standard swap interface), every unprivileged caller can bypass the per-user gate entirely.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
  msg.sender,   // ← direct caller of pool.swap, not the end user
  recipient,
  ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct), and `sender` is whoever called `pool.swap`. When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap` directly:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
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

So `sender` received by the extension is the router address. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

The pool admin faces an impossible choice:

| Admin action | Result |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the standard router interface at all |
| Allowlist the router | **Every** user, allowlisted or not, can bypass the per-user gate via the router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

### Impact Explanation

Any user can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) on a pool whose `SwapAllowlistExtension` has the router allowlisted. The extension sees `sender = router` and passes the check regardless of the end user's identity. The curated pool's access control is fully defeated for all router-mediated swaps. On pools designed to restrict trading to specific counterparties (e.g., KYC-gated, market-maker-only, or compliance-restricted pools), this allows arbitrary users to trade, potentially draining LP value or violating regulatory constraints.

### Likelihood Explanation

The trigger requires the pool admin to have allowlisted the router address. This is the natural and expected configuration: the router is the protocol's standard swap interface, and pool admins who want their allowlisted users to be able to trade through it will allowlist it. The admin has no way to know that doing so opens the gate to all users, because the extension's documentation and interface give no indication that `sender` will be the router rather than the end user. The likelihood is therefore **medium** — it depends on a reasonable admin action, not a malicious or unusual one.

### Recommendation

The `SwapAllowlistExtension` should gate on the economically relevant actor. Two options:

1. **Check `recipient` instead of `sender`**: For swap allowlists the recipient is the address that receives value; however this is also caller-controlled.
2. **Require the end user to pass their identity in `extensionData` and verify it with a signature or on-chain proof**: The extension decodes the end user address from `extensionData` and checks `allowedSwapper[pool][endUser]`. The router must forward the correct `extensionData` per hop.
3. **Document that the router must never be allowlisted and that allowlisted users must call the pool directly**: This is the least-code fix but breaks the standard UX.

The cleanest fix is option 2, which preserves the router UX while correctly gating the end user.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice]  = true   (alice is the intended gated user)
  allowedSwapper[pool][router] = true   (admin adds this so alice can use the router)

Attack:
  bob (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({
      pool: pool,
      recipient: bob,
      zeroForOne: true,
      amountIn: X,
      ...
    })

  Flow:
    router → pool.swap(recipient=bob, ..., extensionData)
      pool: sender = router (msg.sender)
      pool: _beforeSwap(sender=router, ...)
        SwapAllowlistExtension.beforeSwap(sender=router, ...)
          allowedSwapper[pool][router] == true  → passes
      swap executes for bob
      bob receives output tokens

Result: bob, who is not in the allowlist, successfully swaps on the curated pool.
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
