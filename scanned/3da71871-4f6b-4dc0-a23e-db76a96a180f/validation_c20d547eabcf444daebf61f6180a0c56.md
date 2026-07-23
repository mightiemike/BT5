### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, allowing any user to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is documented as gating "swap by swapper address, per pool." Its `beforeSwap` hook receives both `sender` (the direct caller of `pool.swap()`) and `recipient` (the output receiver). It checks only `sender`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract, not the user. Any user can therefore bypass a per-user allowlist by calling the router instead of the pool directly.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

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

`msg.sender` here is the pool (the extension is called by the pool). `sender` is `msg.sender` of the pool's `swap()` call — i.e., whoever called `pool.swap()` directly.

`MetricOmmSimpleRouter.exactInputSingle` calls the pool as:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
```

The pool's `swap()` passes `msg.sender` (the router) as `sender` to the extension:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- router address, not the actual user
    recipient,
    ...
);
```

So the allowlist lookup becomes `allowedSwapper[pool][router]`. If the router is allowlisted (the normal production configuration), every user of the router passes the check regardless of whether their own address is on the allowlist. A pool admin who adds specific user addresses to the allowlist — intending to restrict swaps to KYC'd or whitelisted participants — achieves no restriction for any user who routes through `MetricOmmSimpleRouter`.

The `recipient` parameter (the actual output receiver) is silently discarded (`address,`) in the function signature, so there is no path to recover the real user identity inside the extension.

---

### Impact Explanation

The `SwapAllowlistExtension` is the production access-control primitive for restricting pool swaps. Its bypass means:

- A pool configured for permissioned trading (e.g., institutional or KYC-gated pools) is fully open to any address that uses the router.
- Unauthorized users can execute swaps, draining LP liquidity and collecting output tokens they are not entitled to receive.
- The pool admin's allowlist configuration is silently ineffective — no on-chain error or event signals the bypass.

This is an admin-boundary break: the pool admin's access control is bypassed by an unprivileged path (the standard periphery router).

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the primary user-facing entry point for swaps; virtually all non-technical users interact through it.
- The router must be allowlisted for the pool to be usable at all, so the bypass condition (`allowedSwapper[pool][router] == true`) is the default production state.
- No special knowledge or privilege is required; any user can call `router.exactInputSingle()`.

---

### Recommendation

The extension must identify the real user, not the intermediary. Two options:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted router assumption.
2. **Check `recipient` instead of (or in addition to) `sender`**: For swap allowlists whose intent is to control who receives output, gate on the `recipient` parameter that is already passed to `beforeSwap` but currently ignored.
3. **Require direct pool interaction**: Document that the allowlist only works when users call the pool directly, and remove the router from the allowlist for permissioned pools.

The cleanest fix matching the external bug's recommendation pattern: when `sender` is a known router, decode the real initiator from `extensionData` and apply the allowlist check to that address.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` and adds only `alice` to the allowlist.
2. Pool admin also adds `MetricOmmSimpleRouter` to the allowlist (required for normal operation).
3. `bob` (not on the allowlist) calls `router.exactInputSingle({pool: pool, recipient: bob, ...})`.
4. Router calls `pool.swap(bob, ...)` — pool calls `extension.beforeSwap(sender=router, ...)`.
5. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. `bob` receives output tokens despite not being on the allowlist.
7. Direct call: `pool.swap(bob, ...)` from `bob`'s EOA → `allowedSwapper[pool][bob]` → `false` → reverts `NotAllowedToSwap`.

The bypass is unconditional whenever the router is allowlisted. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L50-60)
```text
  function beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) external returns (bytes4);
```
