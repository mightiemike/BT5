### Title
`SwapAllowlistExtension` checks the router address instead of the end-user, allowing any user to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps on the `sender` parameter, which is `msg.sender` of the pool's `swap()` call. When `MetricOmmSimpleRouter` is the caller, `sender` is the **router address**, not the actual end-user. If the pool admin allowlists the router (the only way to let legitimate users trade through it), every unpermissioned user can bypass the per-user allowlist by routing through `MetricOmmSimpleRouter`.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-L240
_beforeSwap(
  msg.sender,   // ← direct caller of pool.swap(), not the end-user
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol L160-L176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)   // sender = msg.sender of pool.swap()
  )
);
```

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L31-L41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-L80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

At this point `msg.sender` inside the pool is the **router**, so `sender` forwarded to the extension is the router address. The allowlist check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

This creates an irresolvable dilemma for the pool admin:

| Admin choice | Consequence |
|---|---|
| Allowlist the router | Every user — including those not individually allowlisted — can swap by going through the router |
| Do not allowlist the router | Individually allowlisted users cannot use the router at all (DoS on the standard swap path) |

The original user's address is never propagated to the extension; there is no field in the hook signature that carries it.

---

### Impact Explanation

A pool admin who deploys `SwapAllowlistExtension` to create a restricted pool (e.g., KYC-only, private market-maker pool, or regulatory-gated venue) cannot enforce per-user access control when `MetricOmmSimpleRouter` is in use. Any unpermissioned user can execute swaps against the restricted pool by calling `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` on the router. The pool's token reserves are exposed to unrestricted trading, defeating the entire purpose of the allowlist guard and constituting a broken core pool functionality with direct fund-flow consequences (unauthorized parties drain or manipulate a pool that was intended to be access-controlled).

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical swap interface documented and deployed alongside the protocol. Any pool admin who configures `SwapAllowlistExtension` and also wants users to be able to use the standard router must allowlist the router, which immediately opens the bypass to all users. The trigger requires no special privileges, no malicious setup, and no non-standard tokens — only a call to the public router.

---

### Recommendation

The extension hook signature must carry the original end-user's address. Two options:

1. **Add an `originator` field to the `beforeSwap` hook signature** — the pool passes `tx.origin` or a caller-supplied address (verified by the router via `extensionData`) so the allowlist can check the real user.

2. **Require callers to embed their address in `extensionData`** and have `SwapAllowlistExtension` decode and verify it — the router would encode `msg.sender` into `extensionData` before calling the pool, and the extension would check that value instead of `sender`.

Option 1 is cleaner but requires a protocol-level interface change. Option 2 can be done at the extension level but requires the router to cooperate.

At minimum, the `SwapAllowlistExtension` NatSpec and documentation must warn that `sender` is the direct pool caller, not the end-user, and that the extension is ineffective when any intermediary router is allowlisted.

---

### Proof of Concept

**Setup:**
- Pool is deployed with `SwapAllowlistExtension` as `EXTENSION_1` in `BEFORE_SWAP_ORDER`.
- Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is permitted.
- Admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use it.

**Attack (Bob, not allowlisted):**
```solidity
// Bob calls the router directly — no individual allowlist entry needed
router.exactInputSingle(ExactInputSingleParams({
    pool:            restrictedPool,
    recipient:       bob,
    zeroForOne:      true,
    amountIn:        1e18,
    amountOutMinimum: 0,
    priceLimitX64:   0,
    tokenIn:         token0,
    deadline:        block.timestamp,
    extensionData:   ""
}));
```

**Trace:**
1. Router calls `pool.swap(bob, true, ...)` — `msg.sender` inside pool = router.
2. Pool calls `extension.beforeSwap(router, bob, ...)`.
3. Extension checks `allowedSwapper[pool][router]` → `true` → passes.
4. Bob's swap executes against the restricted pool. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
