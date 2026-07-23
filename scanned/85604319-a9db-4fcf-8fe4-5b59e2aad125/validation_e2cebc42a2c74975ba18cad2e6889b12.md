The code path is fully traceable. Let me confirm the exact chain before rendering a verdict.

The full call chain is confirmed. Here is the verdict:

---

### Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the original user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is designed to gate swaps by individual swapper address. However, the `sender` value it receives is always `msg.sender` from the pool's perspective — which is the router contract, not the originating user. Any user can bypass the allowlist by routing through `MetricOmmSimpleRouter` if the router address is allowlisted for the pool.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` in `ExtensionCalling.sol` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether `sender` (the direct pool caller) is allowlisted: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly. The original user's address (`msg.sender`) is stored only in transient callback context for payment settlement and is **never forwarded** to the pool as the swap initiator: [4](#0-3) 

The result is that `sender` arriving at `SwapAllowlistExtension.beforeSwap` is always the router address when the router is used — never the originating user.

This creates an irreconcilable configuration problem for any pool admin:

- **If the router is NOT allowlisted:** allowlisted users cannot swap through the router at all (the router address fails the check).
- **If the router IS allowlisted:** every user on the network can bypass the allowlist by calling the router, because the check passes on the router address regardless of who initiated the call.

---

### Impact Explanation

The `SwapAllowlistExtension` is a scoped production contract whose stated purpose is to "gate `swap` by swapper address, per pool." That invariant is completely broken when `MetricOmmSimpleRouter` is in the call path. A pool operator who deploys a permissioned pool with this extension and allowlists the router (the only way to enable router-based swaps for legitimate users) inadvertently opens the pool to all callers. Unauthorized users can execute swaps in a pool that was explicitly configured to restrict access.

---

### Likelihood Explanation

The router is the primary intended swap interface for end users. Any pool that uses `SwapAllowlistExtension` and wants to support router-based swaps must allowlist the router, triggering the bypass. The attacker needs no special privileges — only the ability to call `MetricOmmSimpleRouter`.

---

### Recommendation

The pool's `swap` interface does not carry the original user's address. To fix this, one of the following approaches is needed:

1. **Pass the original caller through `extensionData`:** The router encodes `msg.sender` into `extensionData` before calling `pool.swap`. `SwapAllowlistExtension.beforeSwap` decodes and checks that address when `extensionData` is non-empty, falling back to `sender` for direct calls.
2. **Add a trusted-forwarder pattern:** Introduce a dedicated field or a separate pool entry point that carries the originating user address, verified by the pool against a known router registry.
3. **Check `recipient` instead of `sender`:** If the pool's design intent is that the recipient is always the economic beneficiary, the extension could check `recipient`. This is a weaker fix and only appropriate if recipient and swapper are always the same.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension, router allowlisted, attacker NOT allowlisted
extension.setAllowedToSwap(address(pool), address(router), true);
// attacker address is NOT in allowedSwapper

// Attack: attacker calls router
vm.prank(attacker); // attacker is not allowlisted
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    ...
}));
// pool.swap is called with msg.sender = router
// _beforeSwap passes sender = router to SwapAllowlistExtension
// allowedSwapper[pool][router] == true → check passes
// swap executes for attacker despite not being allowlisted
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
```

**File:** metric-core/contracts/ExtensionCalling.sol (L160-166)
```text
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
