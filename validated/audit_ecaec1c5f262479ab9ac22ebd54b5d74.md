Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the originating user, allowing any caller to bypass per-pool swap allowlists — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` receives the `sender` argument forwarded by the pool, which is the pool's own `msg.sender` — the router contract — not the end-user who initiated the transaction. When a pool admin allowlists `MetricOmmSimpleRouter` to enable router-mediated swaps, every address on the network can bypass the allowlist by calling the public router. There is no configuration that simultaneously permits router-mediated swaps and enforces per-user allowlist policy.

## Finding Description

**Root cause:** `MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` calls the pool, the pool's `msg.sender` is the router. The router stores the original caller only in transient storage for the payment callback — it does **not** inject the original `msg.sender` into `extensionData`: [4](#0-3) 

The `extensionData` forwarded to the pool is `params.extensionData`, which is caller-supplied and carries no authenticated identity. The allowlist check therefore resolves to `allowedSwapper[pool][router]`, never `allowedSwapper[pool][actual_user]`.

**Existing guards are insufficient:** `allowAllSwappers` and `allowedSwapper` both key on the `sender` argument, which is structurally the router for all router-mediated swaps. There is no secondary check on the originating EOA.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (KYC'd counterparties, whitelisted market makers, or protocol-internal actors) is completely unenforceable for any user who routes through `MetricOmmSimpleRouter`. Any unprivileged address can execute swaps against the pool by calling the public router. This is a direct admin-boundary break: the pool admin's explicit intent to block specific addresses is defeated, with fund-impacting consequences for LP principals who deposited under the assumption that the allowlist was enforced.

## Likelihood Explanation

The router is the primary user-facing entry point deployed alongside the protocol. Any production pool using `SwapAllowlistExtension` that expects normal user interaction through the router will be misconfigured by design. The bypass requires no special privileges, no flash loans, and no multi-step setup — a single `exactInputSingle` call from any address suffices. The condition is triggered by the normal, documented usage pattern.

## Recommendation

The extension must gate the **original end-user**, not the intermediate router. Two viable approaches:

1. **Pass the original caller through `extensionData`:** The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks it. This requires a coordinated convention between router and extension.

2. **Trusted-router registry with attested caller:** The extension maintains a registry of trusted routers; when `sender` is a trusted router, it reads the real caller from a standardized field in `extensionData`.

The simplest safe fix for the current architecture is option 1: the router always appends the original `msg.sender` to `extensionData`, and `SwapAllowlistExtension.beforeSwap` decodes it before performing the allowlist lookup.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (required for any router-mediated swap to pass the check)
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker (not allowlisted) calls:
      router.exactInputSingle({pool: pool, tokenIn: ..., extensionData: "", ...})
  - Router calls pool.swap(recipient, ..., extensionData="")
    with msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - Swap executes successfully for the non-allowlisted attacker

Result:
  - attacker completes a swap on a pool that was supposed to block them.
  - The allowlist invariant is broken for every router-mediated swap.
  - Any address can repeat this with a single public function call.
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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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
