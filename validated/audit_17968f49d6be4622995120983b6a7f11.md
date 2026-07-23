The code confirms the claim. Let me verify the complete call chain:

1. `MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap` [1](#0-0) 

2. `ExtensionCalling._beforeSwap` encodes `sender` directly into the extension call [2](#0-1) 

3. `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` making the router the pool's `msg.sender` [3](#0-2) 

4. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` — `sender` is the router, not the end user [4](#0-3) 

5. The `bytes calldata` (extensionData) parameter in `beforeSwap` is unnamed and completely ignored — no mechanism exists to pass the real user through it [5](#0-4) 

The finding is structurally sound and confirmed by production code.

---

Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Any User to Bypass the Allowlist via the Public Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the pool's `msg.sender` — the immediate caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` is the router address, not the actual end user. If the pool admin allowlists the router (a necessary step for any legitimate user to use the router), every unprivileged user can bypass the allowlist by routing through the same public router contract, completely nullifying the access control.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(msg.sender, recipient, zeroForOne, ...);
```

`ExtensionCalling._beforeSwap` encodes this `sender` directly into the extension call payload. `SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` is used, the router calls `pool.swap(...)` directly, making `sender = router`. The extension then checks `allowedSwapper[pool][router]`, not the actual end user.

For Alice (a legitimate allowlisted user) to use the router, the pool admin **must** also call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, Bob (not allowlisted) can call `router.exactInputSingle({pool: pool, ...})`, the router calls `pool.swap(...)`, the pool calls `extension.beforeSwap(router, ...)`, and the check passes because `allowedSwapper[pool][router] == true`. The `extensionData` bytes parameter in `beforeSwap` is unnamed and entirely ignored — there is no existing mechanism to recover the real end user.

The same structural issue applies to multi-hop `exactInput` where intermediate hops use `address(this)` (the router) as the payer/sender.

## Impact Explanation
The `SwapAllowlistExtension` access control is completely nullified for any pool whose admin allowlists the router. Unauthorized users gain full swap access to pools intended to be restricted to specific counterparties (e.g., institutional-only pools, pools with specific pricing assumptions). This constitutes a broken core pool functionality — the allowlist invariant that only approved addresses may swap is violated — and can result in unauthorized extraction of value or price manipulation against LP assets, constituting a direct loss of protocol integrity and LP funds.

## Likelihood Explanation
The trigger requires no special privileges beyond the router being allowlisted, which is a **required operational state** for the allowlist to coexist with router usage. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` targeting the restricted pool. The router is a public, permissionless contract. The condition is reachable in any realistic deployment where the pool admin intends to support both allowlist enforcement and router-mediated swaps.

## Recommendation
The extension must check the actual end user, not the intermediate router. Two viable approaches:

1. **Pass the real user in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it, also verifying that the immediate `sender` is a trusted router via a registry.
2. **Trusted-router registry in the extension**: When `sender` is a known trusted router, the extension reads the actual user from `extensionData`; otherwise it checks `sender` directly.

## Proof of Concept
```
1. Admin deploys pool with SwapAllowlistExtension configured.
2. Admin calls setAllowedToSwap(pool, alice, true)   // alice is the intended user
3. Admin calls setAllowedToSwap(pool, router, true)  // required for alice to use the router
4. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: bob, ...})
5. Router calls pool.swap(bob, ...) with msg.sender = router.
6. Pool calls _beforeSwap(router, ...) → extension.beforeSwap(router, ...).
7. Extension checks: allowedSwapper[pool][router] == true → passes.
8. Bob's swap executes successfully despite not being allowlisted.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
```

**File:** metric-core/contracts/ExtensionCalling.sol (L160-165)
```text
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
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
