Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the end-user, allowing any router caller to bypass per-pool swap allowlists — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap` call. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract address, not the end user. A pool admin who allowlists the router to enable router-mediated swaps for permitted users inadvertently opens the gate to every public caller of the router, fully defeating the per-user allowlist.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs the identity check as:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (mapping key) and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`. That argument originates from `MetricOmmPool.swap` at L231:

```solidity
_beforeSwap(msg.sender, recipient, ...);
```

So `sender` in the extension is `msg.sender` of the pool's `swap` call. In `MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`), the pool is called directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient, params.zeroForOne, ..., params.extensionData
);
```

`msg.sender` to the pool is the router contract. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The router does not encode the original `msg.sender` into `extensionData`, and the extension ignores `extensionData` entirely (the parameter is unnamed). There is no mechanism for the extension to recover the true end-user identity.

A pool admin who wants to allow specific users to swap through the router has no selective knob: the only available action is `setAllowedToSwap(pool, router, true)`, which grants every public caller of the router access to the restricted pool.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC-verified addresses, institutional traders, whitelisted market makers) is fully bypassed once the pool admin allowlists the router. Any EOA can call `MetricOmmSimpleRouter.exactInputSingle` and trade in the restricted pool. LPs who deposited under the assumption that only vetted counterparties would trade against them are exposed to unrestricted adverse selection, directly eroding LP principal. This fits the "admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" criterion.

## Likelihood Explanation
The trigger is a pool admin allowlisting the router — a natural and expected configuration step for any pool that wants to support router-mediated swaps for its permitted users. The admin has no other mechanism to enable router access for specific users; the only available knob is the router address itself. The mistake is not malicious; it is a predictable consequence of the extension's design. Once the router is allowlisted, the bypass requires no special privilege: any EOA calls `exactInputSingle` on the public router.

## Recommendation
The extension must receive and check the original end-user identity, not the intermediary's address. Two complementary fixes:

1. **Pass the original caller through `extensionData`**: The router should encode `msg.sender` into `extensionData` before forwarding to the pool. The extension decodes and checks that address. This requires a convention between the router and the extension.

2. **Check `recipient` instead of `sender` for swap allowlists**: For many use cases the intended gate is the economic beneficiary (`recipient`), not the technical caller. The extension could be parameterised to choose which field to gate.

Minimal diff for option 1 in the extension:

```diff
-function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
+function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata extensionData)
     external view override returns (bytes4)
 {
+    address effectiveSender = extensionData.length >= 20
+        ? abi.decode(extensionData, (address))
+        : sender;
-    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
+    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][effectiveSender]) {
         revert IMetricOmmPoolActions.NotAllowedToSwap();
     }
```

And in the router, encode `msg.sender` as the first word of `extensionData` before forwarding.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` as the `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
3. Pool admin does **not** allowlist `attacker` EOA: `allowedSwapper[pool][attacker] == false`.
4. `attacker` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender=router, ...)` at L231.
7. `ExtensionCalling._beforeSwap` encodes `sender=router` and calls the extension.
8. `beforeSwap(sender=router, ...)` checks `allowedSwapper[pool][router] == true` → passes.
9. The swap executes. `attacker` has traded in a pool they were never meant to access.