### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the actual user. If the pool admin allowlists the router address (a natural step to enable router-mediated swaps for their curated pool), every user — including those not on the allowlist — can bypass the guard entirely.

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument, which the pool sets to `msg.sender` of the `swap` call:

```solidity
// MetricOmmPool.sol — swap()
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
```

The extension then checks:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

`msg.sender` here is the pool; `sender` is the router. So the check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(recipient, ...)` directly with no mechanism to forward the originating user's address to the extension:

```solidity
// MetricOmmSimpleRouter.sol
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
IMetricOmmPoolActions(params.pool).swap(
    params.recipient, params.zeroForOne, ..., params.extensionData
);
```

The actual user (`msg.sender` of the router call) is stored only in transient callback context for payment settlement — it is never surfaced to the extension layer.

**Decoupling scenario (M-23 analog):** A pool admin deploys a curated pool with `SwapAllowlistExtension` and allowlists specific user addresses. To let those users trade via the router, the admin also calls `setAllowedToSwap(pool, router, true)`. At this point the allowlist is permanently decoupled: the extension's stored configuration (`allowedSwapper[pool][router] = true`) no longer matches the intended policy (gate individual users), exactly as `settings.treasury` diverged from the actual owner in M-23. Any unprivileged user can now call `router.exactInputSingle(pool, ...)` and the guard passes unconditionally.

### Impact Explanation

Once the router is allowlisted (the only way to let allowlisted users trade via the router), the allowlist provides zero protection. Any address can swap in the curated pool. Depending on pool design this enables:

- Unauthorized counterparties trading in a pool intended for specific participants, draining LP-owned liquidity at oracle prices.
- Front-running or sandwich attacks by actors the pool admin explicitly excluded.
- If the pool is used as a private OTC venue, complete loss of the curation guarantee that LPs deposited under.

The `DepositAllowlistExtension` does not share this flaw (it checks `owner`, the economic beneficiary, which is correctly forwarded regardless of intermediary). The swap path has no equivalent forwarding.

### Likelihood Explanation

The trigger requires the pool admin to allowlist the router — a natural and expected operational step for any curated pool whose users are expected to interact via the standard periphery. The admin has no way to simultaneously (a) allow allowlisted users to use the router and (b) keep the allowlist effective, because the extension architecture provides no mechanism to distinguish router-forwarded user identity from the router itself. The misconfiguration is therefore not a mistake but an architectural inevitability for any curated pool that supports router access.

### Recommendation

`SwapAllowlistExtension.beforeSwap` should gate on the actual economic actor. Two options:

1. **Decode user identity from `extensionData`**: Require the router to ABI-encode the originating user address into `extensionData` for allowlisted pools, and have the extension decode and check that address instead of (or in addition to) `sender`.

2. **Check `recipient` as a proxy**: For swap allowlists, gate on `recipient` rather than `sender` when `sender` is a known router, or require the pool admin to allowlist individual users and never the router.

The cleanest fix is option 1: the router should forward `msg.sender` inside `extensionData` for allowlist-aware pools, and `SwapAllowlistExtension` should decode and verify it, falling back to `sender` only when no user identity is provided.

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, userA, true)` and `setAllowedToSwap(pool, router, true)` (to let `userA` use the router).
3. `userB` (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
4. Pool calls `_beforeSwap(router, ...)` → extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
5. `userB` successfully swaps in the curated pool despite not being on the allowlist.

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
