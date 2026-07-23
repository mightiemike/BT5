### Title
`SwapAllowlistExtension` checks the router address as the swapper identity, allowing any user to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the original user. If the pool admin allowlists the router (the only way to let legitimate users use it), every user — including non-allowlisted ones — can bypass the curated pool's access control.

---

### Finding Description

**Root cause — wrong actor binding in `SwapAllowlistExtension.beforeSwap`:**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← this is the router when the user routes through it
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards it verbatim to the extension:

```solidity
// ExtensionCalling.sol:160-176
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
```

`SwapAllowlistExtension.beforeSwap` then checks that forwarded address:

```solidity
// SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol:72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
);
```

The pool sees `msg.sender = router`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`. The original user's identity is never forwarded to the pool or the extension.

**The dilemma this creates for pool admins:**

| Admin action | Result |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router — broken core functionality |
| Allowlist the router | Every user can bypass the allowlist by routing through it |

There is no configuration that simultaneously allows legitimate users to use the router and blocks non-allowlisted users.

**Concrete bypass path:**

1. Pool admin deploys pool with `SwapAllowlistExtension`, sets `allowAllSwappers[pool] = false`, and sets `allowedSwapper[pool][alice] = true`.
2. To let Alice use the router, admin also sets `allowedSwapper[pool][router] = true`.
3. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(...)` → pool passes `sender = router` → extension checks `allowedSwapper[pool][router] = true` → passes.
5. Bob's swap executes on the curated pool.

---

### Impact Explanation

A curated pool's swap allowlist is completely ineffective when the `MetricOmmSimpleRouter` is in use. LPs who provide liquidity to a curated pool at tighter spreads — trusting that only vetted counterparties can trade — are exposed to arbitrary, potentially adversarial traders. This constitutes:

- **Admin-boundary break**: the pool admin's allowlist policy is bypassed by an unprivileged path (the public router).
- **Broken core pool functionality**: the allowlist extension, a production guard, fails to enforce its invariant on the supported periphery path.
- **Direct LP fund risk**: non-allowlisted users can extract value from LPs who expected a restricted trading environment.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary user-facing swap interface; most users will route through it.
- A pool admin who wants legitimate users to use the router has no choice but to allowlist the router address, which opens the bypass to everyone.
- No special privileges or unusual conditions are required — any EOA can call the router.

---

### Recommendation

The extension must gate the **economically relevant actor**, not the immediate pool caller. Two sound approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated convention between the router and the extension.

2. **Check `recipient` instead of `sender`** (if the pool's design intent is to gate who receives output): use the `recipient` argument already forwarded to `beforeSwap`.

3. **Preferred — gate at the router level**: Add an allowlist check inside `MetricOmmSimpleRouter` before calling `pool.swap`, so the router enforces the same policy using the original `msg.sender`. The extension then only needs to block direct pool calls from non-allowlisted addresses.

The core invariant that must hold: *a curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it.*

---

### Proof of Concept

```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Pool deployed with ext as beforeSwap extension

// Admin: only alice can swap
ext.setAllowedToSwap(pool, alice, true);
// Admin: allowlist router so alice can use it
ext.setAllowedToSwap(pool, address(router), true);

// Attack: bob (not allowlisted) routes through the router
vm.prank(bob);
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: bob,
    zeroForOne: true,
    amountIn: 1e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    extensionData: ""
}));
// ✓ succeeds — bob bypassed the allowlist
// Extension checked allowedSwapper[pool][router] = true, not allowedSwapper[pool][bob]
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
