### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, allowing any unprivileged swapper to bypass the per-pool allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If the pool admin allowlists the router (the only way to permit any router-mediated swap for allowlisted users), every user — including those explicitly excluded from the allowlist — can bypass the gate by routing through the router.

---

### Finding Description

**Root cause — wrong identity bound to the guard:**

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    zeroForOne,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol line 160-176
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks that forwarded `sender` against its per-pool mapping:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
```

At this point `msg.sender` inside the pool is the **router**, so `sender` delivered to the extension is the router address. The extension evaluates `allowedSwapper[pool][router]`, never touching the actual user's address.

**Bypass path:**

1. Pool admin deploys a pool with `SwapAllowlistExtension` and allowlists a set of KYC'd users.
2. To let those users swap via the router (the standard periphery), the admin must also call `setAllowedToSwap(pool, router, true)`.
3. Once the router is allowlisted, any address — including those explicitly excluded — can call `router.exactInputSingle(...)` and the extension passes, because it only sees the router.

The `DepositAllowlistExtension` does not share this flaw: it gates on `owner` (the economic actor), which the pool passes explicitly and which the `MetricOmmPoolLiquidityAdder` cannot forge.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., institutional or KYC'd participants) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Unauthorized swappers can drain pool liquidity at oracle prices, extract value from LP positions, and interact with pools that were contractually or regulatorily required to be closed to them. This is a direct loss of LP assets and a broken core pool invariant (access control).

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical, publicly deployed periphery swap entry point. Any pool admin who wants allowlisted users to be able to use the router must allowlist the router itself — there is no other mechanism. The moment that allowlist entry is added, the bypass is live for every address. The trigger requires no special privilege, no flash loan, and no unusual token behavior; a standard `exactInputSingle` call suffices.

---

### Recommendation

The extension must be able to recover the **original user** from the call context. Two options:

1. **Pass the real initiator through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks it. This requires a convention between the router and the extension.

2. **Check `sender` against a router-aware allowlist**: Introduce a separate mapping `allowedRouter` and, when `sender` is a known router, require the extension data to carry a signed or otherwise authenticated user identity.

The simplest safe default is to treat any call whose `sender` is not in `allowedSwapper` as blocked, and document that router-mediated swaps require the router to be allowlisted — with an explicit warning that doing so opens the gate to all users.

---

### Proof of Concept

```
Setup:
  - Pool P with SwapAllowlistExtension E
  - Admin allowlists alice: setAllowedToSwap(P, alice, true)
  - Admin allowlists router R so alice can use it: setAllowedToSwap(P, R, true)
  - bob is NOT allowlisted

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})
    → router calls P.swap(recipient, zeroForOne, ...)
    → pool calls _beforeSwap(msg.sender=router, ...)
    → extension checks allowedSwapper[P][router] == true  ✓
    → swap executes; bob receives output tokens

Result:
  bob, an explicitly excluded address, successfully swaps against the
  restricted pool. The allowlist invariant is broken.
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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
