Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End-User Identity, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` sets to `msg.sender` — the immediate caller of the pool. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` is the router contract, not the originating user. Any pool admin who allowlists the router to enable standard UX simultaneously grants every on-chain user the ability to bypass the allowlist by routing through the router.

## Finding Description
The call chain is confirmed by the production code:

1. `MetricOmmPool.swap` passes `msg.sender` verbatim as `sender` to `_beforeSwap` (`MetricOmmPool.sol` L230–231). When the router calls the pool, `msg.sender` is the router address.

2. `ExtensionCalling._beforeSwap` encodes that `sender` value as the first positional argument of the `beforeSwap` ABI call (`ExtensionCalling.sol` L162–165), forwarding it unchanged to every configured extension.

3. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the router address (`SwapAllowlistExtension.sol` L37). The originating user's address is never examined.

4. `MetricOmmSimpleRouter.exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` directly (`MetricOmmSimpleRouter.sol` L72–80). The router does not forward the original `msg.sender` into the pool's `sender` slot; it only passes `params.recipient` as the output recipient. The router becomes the pool's `msg.sender`.

The result is a binary impossible choice for the pool admin: allowlist specific user addresses (those users are blocked when using the router because the router address is not allowlisted) or allowlist the router address (every user on-chain can bypass the allowlist). No configuration simultaneously allows router-mediated swaps for approved users while blocking unapproved users.

Contrast with `DepositAllowlistExtension.beforeAddLiquidity`, which checks `allowedDepositor[msg.sender][owner]` (`DepositAllowlistExtension.sol` L38) — `owner` is an explicit caller-supplied argument that identifies the economic actor, not the intermediary. The swap allowlist lacks an equivalent pattern.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (KYC'd addresses, institutional market makers, whitelisted protocols) loses that restriction entirely once the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` and trade against the pool's LP liquidity. LP providers who deposited under the assumption that only approved counterparties could trade against them are exposed to unapproved flow, causing direct LP principal loss through adverse selection or unauthorized fee extraction. This is broken core allowlist functionality with direct LP-principal exposure on any curated pool that uses the router — High severity.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap entry point. Any pool admin who wants to support standard UX must allowlist the router, which is the exact configuration that triggers the bypass. The attacker requires no special privilege — a single public router call suffices. The condition is not hypothetical; it is the expected production configuration for any pool that uses both the allowlist extension and the router.

## Recommendation
The extension must gate the economically relevant actor, not the intermediary. Two sound approaches:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks that value. This requires a convention between router and extension.

2. **Dedicated caller-identity field**: Add a caller-identity field to the `beforeSwap` interface (analogous to `owner` in `beforeAddLiquidity`) that the pool sets to `msg.sender` and the router is required to forward the original user address through `extensionData` or a separate mechanism.

The `DepositAllowlistExtension` pattern — checking an explicit `owner` argument rather than `sender` — is the correct model to follow.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured on beforeSwap.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (necessary to allow any router-mediated swap).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  1. attacker (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({
         pool: pool,
         recipient: attacker,
         ...
       })
  2. Router calls pool.swap(attacker, ...) with msg.sender = router.
  3. Pool calls _beforeSwap(sender=router, ...).
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true.
  5. Swap executes. Attacker receives output tokens.

Result:
  attacker bypassed the allowlist and traded against the curated pool's
  LP liquidity without being an approved swapper.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
```

**File:** metric-core/contracts/ExtensionCalling.sol (L162-165)
```text
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L38-39)
```text
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
```
