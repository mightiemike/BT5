The code confirms the vulnerability. Here is the analysis:

**`SwapAllowlistExtension.beforeSwap`** checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the argument forwarded by the pool from its own `msg.sender` — i.e., the direct caller of `pool.swap()`. [1](#0-0) 

**`ExtensionCalling._beforeSwap`** confirms `sender` (pool's `msg.sender`) is forwarded verbatim to the extension. [2](#0-1) 

**`MetricOmmSimpleRouter.exactInputSingle`** calls `pool.swap(params.recipient, ...)` with no injection of the real user into `extensionData` — the router is the pool's `msg.sender`, and `extensionData` is passed through unmodified from user input. [3](#0-2) 

The bypass is real and complete.

---

Audit Report

## Title
Swap Allowlist Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass Per-User Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension evaluates the router's address against the allowlist rather than the actual end-user. Any pool that allowlists the router to support normal periphery UX inadvertently grants every unprivileged user the ability to bypass the per-user allowlist restriction.

## Finding Description
The pool's `_beforeSwap` dispatcher in `ExtensionCalling.sol` (L159–176) forwards `sender` — the pool's own `msg.sender` — to every configured extension via `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))`. `SwapAllowlistExtension.beforeSwap` (L37) then checks `allowedSwapper[msg.sender][sender]`, i.e., `allowedSwapper[pool][router]` when the router is the caller. `MetricOmmSimpleRouter.exactInputSingle` (L72–80) calls `pool.swap(params.recipient, ...)` with `extensionData` passed through unmodified from user input — the router never encodes the real `msg.sender` into `extensionData`. There is no existing guard that recovers the original user identity. A pool admin who allowlists the router (required for any router-mediated swap to work) simultaneously opens the pool to all users, because any address can call the public router and have the allowlist check pass against the router's allowlisted address.

## Impact Explanation
A pool admin deploying `SwapAllowlistExtension` to restrict swaps to a curated set (e.g., KYC'd counterparties, whitelisted market makers) and also allowlisting the router for normal UX inadvertently opens the pool to all users. This is an admin-boundary break: an unprivileged path (the public router) bypasses a pool-admin-configured guard, enabling unauthorized trading in pools not designed for open access. Depending on pool configuration, this can result in unauthorized extraction of liquidity or execution of trades against oracle-anchored prices in a restricted pool.

## Likelihood Explanation
`MetricOmmSimpleRouter` is a public, permissionless contract requiring no special privilege to call. The only precondition is that the pool admin has allowlisted the router — a natural and expected configuration for any pool supporting standard periphery UX. The attacker needs only the swap input tokens and the pool address.

## Recommendation
The extension must check the actual end-user, not the intermediary. Two sound approaches: (1) Have the router encode the original `msg.sender` into `extensionData`; the extension decodes and verifies it — acceptable since the router is a known, audited periphery contract. (2) Extend the extension to detect when `sender` is the known router address and, in that case, gate on `recipient` instead, verifying this holds for all router call paths. A registry of trusted routers in the extension would make this robust.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin calls setAllowedToSwap(pool, alice, true)
  - Pool admin calls setAllowedToSwap(pool, router_address, true)  ← required for router UX
  - Pool admin does NOT allowlist bob

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool, recipient: bob, ...})
  2. Router calls pool.swap(recipient=bob, ...) — pool sees msg.sender = router
  3. Pool calls _beforeSwap(sender=router, recipient=bob, ...)
  4. Extension checks allowedSwapper[pool][router] → TRUE
  5. Swap proceeds — bob has bypassed the per-user allowlist

Result:
  bob, a non-allowlisted address, successfully swaps in a pool the admin
  intended to restrict to alice only.
```
Foundry test: deploy pool + extension, allowlist `[alice, router]`, attempt swap as `bob` via router, assert it succeeds; then attempt direct swap as `bob`, assert it reverts with `NotAllowedToSwap`.

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-core/contracts/ExtensionCalling.sol (L159-176)
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
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
