All four code paths are confirmed in the repository. Let me verify the complete chain:

1. `MetricOmmPool.swap` passes `msg.sender` (= router) as `sender` to `_beforeSwap` [1](#0-0) 

2. `ExtensionCalling._beforeSwap` forwards `sender` unchanged to every configured extension [2](#0-1) 

3. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]` where `msg.sender` = pool and `sender` = router, never the original user [3](#0-2) 

4. `MetricOmmSimpleRouter.exactInputSingle` stores the original `msg.sender` only in transient callback context for payment; the pool's `swap` call sees `msg.sender` = router [4](#0-3) 

---

Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` equal to `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, that value is the router contract address, not the originating user. A pool admin who allowlists the router to enable router-based swaps for their curated users inadvertently grants unrestricted swap access to every caller of the public router, fully defeating the per-pool allowlist invariant.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap` (MetricOmmPool.sol L230–240). `ExtensionCalling._beforeSwap` encodes that value unchanged into the `abi.encodeCall` forwarded to every configured extension (ExtensionCalling.sol L149–177). `SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` = pool address and `sender` = router address. The guard evaluates `allowedSwapper[pool][router]`, never `allowedSwapper[pool][originalUser]`.

In `MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`), the original `msg.sender` is stored only in transient callback context via `_setNextCallbackContext` for payment settlement (MetricOmmSimpleRouter.sol L71). It is never surfaced to the pool or the extension. The pool's `swap` call sees `msg.sender` = router.

**Exploit path:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` on `beforeSwap` to restrict trading to curated addresses.
2. Admin calls `setAllowedToSwap(pool, router, true)` — the only available mechanism to permit router-based swaps for their allowlisted users.
3. Unprivileged attacker calls `router.exactInputSingle({pool: pool, ...})`.
4. Pool calls `_beforeSwap(router, ...)` → extension checks `allowedSwapper[pool][router]` → `true` → swap executes.
5. Attacker's individual allowlist status is never consulted; the swap succeeds.

The same structural flaw applies to all four router entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`), as all call `pool.swap(...)` with `msg.sender` = router.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., a KYC-gated pool, a private institutional pool, or a pool with preferential pricing for specific counterparties) is fully open to any user who routes through `MetricOmmSimpleRouter`. Non-allowlisted users can execute swaps against LP positions at oracle-derived prices that the pool admin intended to reserve for specific counterparties. This is a broken core pool access-control invariant with direct LP fund-impact potential: LP assets are exposed to unrestricted counterparties at prices set for a restricted audience.

## Likelihood Explanation
The scenario is triggered by a natural and expected admin action. There is no mechanism in the system to express "allow these specific users to swap via the router" without allowlisting the router address itself. Any pool admin who deploys `SwapAllowlistExtension` and wants their allowlisted users to access the pool through the public router must take exactly the action that silently opens the pool to all users. The attacker requires no special privileges — only the ability to call the public router.

## Recommendation
The original user's address must be surfaced to the extension. Concrete options:
1. **Encode original caller in `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap`; the extension decodes and verifies it. Requires trust that `extensionData` is not spoofed by a direct pool caller — can be enforced by checking `inSwap()` returns a non-zero address and cross-referencing a router-signed context.
2. **Expose original swapper via pool interface**: Add a `swapper` field to the router's transient context and expose it via a dedicated interface that the extension can query from the pool's `inSwap()` context during the `beforeSwap` hook.
3. **Documentation + separate per-user router allowlist**: Explicitly document that allowlisting the router grants unrestricted access, and provide a separate mechanism (e.g., a router-side allowlist that gates `exactInputSingle` etc.) so pool admins can restrict which users the router will forward to a given pool.

## Proof of Concept
1. Deploy `SwapAllowlistExtension` and a pool with it configured on `beforeSwap`.
2. Admin calls `setAllowedToSwap(pool, address(router), true)`.
3. Attacker (not individually allowlisted) calls `router.exactInputSingle({pool: pool, recipient: attacker, ...})`.
4. Pool invokes `_beforeSwap(address(router), ...)` → extension evaluates `allowedSwapper[pool][router]` = `true` → no revert.
5. Swap executes; attacker receives output tokens from a pool they are not individually authorized to trade on.

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
